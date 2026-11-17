"""News and market events.

  * Google News RSS (Kenya edition) - market, company and economy headlines, each with its
    publisher, publication date and a link to the original article
  * Central Bank of Kenya RSS        - CBK press releases (MPC rate decisions, bond auctions)
  * myStocks corporate calendar      - dividend declarations, book closures, payments
"""
import html
import re
import urllib.parse
from datetime import datetime
from email.utils import parsedate_to_datetime

from nse import _get, MYSTOCKS

NEWS_TTL = 20 * 60
CBK_FEED = "https://www.centralbank.go.ke/feed/"

TOPICS = {
    "market": "\"Nairobi Securities Exchange\" OR \"NSE-listed\" OR \"NSE All Share\"",
    "economy": "Kenya (inflation OR \"interest rates\" OR \"Treasury bills\" OR shilling OR GDP) economy",
    "cbk": "\"Central Bank of Kenya\" (rate OR MPC OR monetary OR inflation OR lending)",
    "earnings": "Kenya (\"profit after tax\" OR \"net profit\" OR earnings) NSE-listed OR Plc results",
    "dividends": "dividend (\"Nairobi Securities Exchange\" OR NSE) Kenya shareholders",
}


def _rss_items(xml):
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(name):
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item, re.S)
            if not m:
                return ""
            v = m.group(1).strip()
            if v.startswith("<![CDATA["):
                v = v[9:-3]
            return html.unescape(v).strip()
        title, link, pub = tag("title"), tag("link"), tag("pubDate")
        source = tag("source")
        try:
            dt = parsedate_to_datetime(pub)
            pub_iso = dt.isoformat()
        except (TypeError, ValueError):
            pub_iso = None
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        desc = re.sub(r"<[^>]+>", " ", tag("description"))
        out.append({"title": title, "url": link, "published": pub_iso, "source": source or None,
                    "summary": " ".join(html.unescape(desc).split())[:300] or None})
    return out


def google_news(query, days=14, limit=20):
    q = urllib.parse.urlencode({"q": f"{query} when:{days}d", "hl": "en-KE", "gl": "KE", "ceid": "KE:en"})
    items = _rss_items(_get("https://news.google.com/rss/search?" + q, NEWS_TTL))
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    return items[:limit]


def topic(name, limit=20):
    return google_news(TOPICS[name], limit=limit)


def company(name, symbol, limit=12):
    clean = re.sub(r"\b(Plc|PLC|Ltd|Limited|Holdings|Group|Company|Co\.)\b", "", name).strip(" .,&")
    return google_news(f"\"{clean}\" Kenya", days=30, limit=limit)


def cbk(limit=15):
    items = _rss_items(_get(CBK_FEED, NEWS_TTL))
    for i in items:
        i["source"] = "Central Bank of Kenya"
    return items[:limit]


def corporate_calendar():
    """Market-wide dividend / book-closure / payment events, newest first."""
    page = _get(f"{MYSTOCKS}/m/calendar", NEWS_TTL)
    out = []
    for block in re.split(r"<h3>", page)[1:]:
        d = block.split("</h3>")[0].strip()
        try:
            iso = datetime.strptime(d, "%b %d %Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        for sym, text in re.findall(r'<div><a href="/stock=([A-Z0-9-]+)">[^<]*</a>\s*([^<]+)</div>', block):
            company, _, event = html.unescape(text).partition(":")
            amt = re.search(r"KES ([\d.,]+)", event)
            out.append({"date": iso, "symbol": sym, "company": company.strip(), "event": event.strip(),
                        "kind": ("announcement" if "Announced" in event else
                                 "book closure" if "Book closure" in event else
                                 "payment" if "Payment" in event else "other"),
                        "amount": float(amt.group(1).replace(",", "")) if amt else None,
                        "source": "live.mystocks.co.ke"})
    return out
