"""User accounts and login sessions.

Passwords: PBKDF2-HMAC-SHA256, 310,000 iterations, per-user random salt (stdlib hashlib).
Sessions: random 32-byte tokens stored in the database, sent as an HttpOnly cookie.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import db

ITERATIONS = 310_000
SESSION_DAYS = 30
COOKIE = "ksa_session"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")

_attempts = {}  # key -> [timestamps] for simple login throttling
_attempts_lock = threading.Lock()


class AuthError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def _now():
    return datetime.now(timezone.utc)


def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"


def check_password(pw, stored):
    try:
        _, iters, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, AttributeError):
        return False


def _throttle(key, limit=8, window=900):
    now = time.time()
    with _attempts_lock:
        hits = [t for t in _attempts.get(key, []) if now - t < window]
        if len(hits) >= limit:
            raise AuthError("Too many attempts. Try again in a few minutes.", 429)
        hits.append(now)
        _attempts[key] = hits


def register(email, name, password, ip=""):
    email = (email or "").strip().lower()
    name = (name or "").strip()[:80]
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    if not name:
        raise AuthError("Enter your name.")
    if len(password or "") < 8:
        raise AuthError("Password must be at least 8 characters.")
    _throttle("register:" + ip, limit=10, window=3600)
    if db.one("SELECT id FROM users WHERE email = ?", (email,)):
        raise AuthError("An account with that email already exists.", 409)
    with db.tx() as t:
        row = t.one("INSERT INTO users (email, name, pw_hash, created_at) VALUES (?, ?, ?, ?) RETURNING id",
                    (email, name, hash_password(password), _now().isoformat()))
        uid = row["id"]
        t.run("INSERT INTO portfolios (user_id, name, created_at) VALUES (?, ?, ?)",
              (uid, "My portfolio", _now().isoformat()))
    return new_session(uid), uid


def login(email, password, ip=""):
    email = (email or "").strip().lower()
    _throttle("login:" + ip)
    _throttle("login:" + email)
    u = db.one("SELECT id, pw_hash FROM users WHERE email = ?", (email,))
    if not u or not check_password(password or "", u["pw_hash"]):
        raise AuthError("Wrong email or password.", 401)
    return new_session(u["id"]), u["id"]


def new_session(uid):
    token = secrets.token_urlsafe(32)
    db.run("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
           (token, uid, (_now() + timedelta(days=SESSION_DAYS)).isoformat()))
    return token


def logout(token):
    if token:
        db.run("DELETE FROM sessions WHERE token = ?", (token,))


def user_for(token):
    if not token:
        return None
    r = db.one("SELECT u.id, u.email, u.name, u.email_alerts, s.expires_at FROM sessions s "
               "JOIN users u ON u.id = s.user_id WHERE s.token = ?", (token,))
    if not r:
        return None
    if r["expires_at"] < _now().isoformat():
        logout(token)
        return None
    return {"id": r["id"], "email": r["email"], "name": r["name"], "email_alerts": bool(r["email_alerts"])}


def change_password(uid, old, new):
    u = db.one("SELECT pw_hash FROM users WHERE id = ?", (uid,))
    if not u or not check_password(old or "", u["pw_hash"]):
        raise AuthError("Current password is wrong.", 401)
    if len(new or "") < 8:
        raise AuthError("New password must be at least 8 characters.")
    db.run("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(new), uid))


def purge_expired():
    db.run("DELETE FROM sessions WHERE expires_at < ?", (_now().isoformat(),))
