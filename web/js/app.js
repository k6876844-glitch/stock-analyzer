// Entry point: header widgets, routes, startup.
import { addRoute, router, initTheme, initSearch, loadUser, loadStocks } from "./core.js";
import { dashboard, stocksPage, stockPage, comparePage, screenerPage, newsPage } from "./views-market.js";
import { analystPage } from "./views-analyst.js";
import { loginPage, registerPage, accountPage, portfolioPage, watchlistPage } from "./views-user.js";

addRoute("/", dashboard);
addRoute("/stocks", stocksPage);
addRoute("/stock/([A-Za-z0-9]{1,10})", stockPage);
addRoute("/compare", comparePage);
addRoute("/screener", screenerPage);
addRoute("/news", newsPage);
addRoute("/analyst", analystPage);
addRoute("/portfolio", portfolioPage, { auth: true });
addRoute("/watchlist", watchlistPage, { auth: true });
addRoute("/account", accountPage, { auth: true });
addRoute("/login", loginPage);
addRoute("/register", registerPage);

initTheme();
initSearch();
window.addEventListener("hashchange", router);
Promise.all([loadUser(), loadStocks()]).finally(router);
