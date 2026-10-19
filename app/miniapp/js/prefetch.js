/* Ранний старт: запрос /api/bootstrap уходит из <head>, параллельно с загрузкой CSS/JS.
   Платформа и initData берутся прямо из URL (#tgWebAppData / #WebAppData) — SDK для этого не нужен.
   Результат подхватывает app.js (window.AKVAREL_BOOT). */
(function () {
  var cfg = window.AKVAREL_CONFIG || {};
  var base = String(cfg.apiBase || "").replace(/\/$/, "");

  function safe(fn) { try { return fn(); } catch (_) { return undefined; } }
  // Telegram/MAX дописывают свои параметры к уже имеющемуся фрагменту ("#/mine?tgWebAppData=…",
  // "#/mine&tgWebAppData=…", "#/mine#tgWebAppData=…") — ищем имя при любом разделителе.
  function hashParam(name) {
    var m = location.hash.match(new RegExp("(?:^#|[#?&])" + name + "=([^&#]*)"));
    if (!m) return "";
    try { return decodeURIComponent(m[1]); } catch (_) { return m[1]; }
  }
  var ss = function (k) { return safe(function () { return sessionStorage.getItem(k); }) || ""; };

  var platform = null, initData = "";
  var d = hashParam("tgWebAppData") || safe(function () { return JSON.parse(ss("__telegram__initParams") || "{}").tgWebAppData; }) || "";
  if (d) { platform = "telegram"; initData = d; }
  else {
    d = hashParam("WebAppData") || ss("WebAppData");
    if (d) { platform = "max"; initData = d; }
  }

  var body = { platform: platform || "telegram", init_data: initData };
  if (!platform) {
    // Только для DEV_MODE: проверка в обычном браузере
    var q = new URLSearchParams(location.search);
    var staff = q.has("staff");
    body.user_id = Number(q.get("uid")) || (staff ? 9001 : 1001);
    if (staff) body.full_name = q.get("name") || "Мастер";
    else if (q.get("name")) body.full_name = q.get("name");
    if (q.get("platform") === "max") body.platform = "max";
  }

  if (base) {
    var l = document.createElement("link");
    l.rel = "preconnect"; l.href = base; l.crossOrigin = "anonymous";
    document.head.appendChild(l);
  }

  // text/plain + без кастомных заголовков = «простой» CORS-запрос без preflight.
  // Исключение — localtunnel: ему нужен заголовок, иначе отдаёт страницу-заглушку.
  var headers = { "Content-Type": "text/plain;charset=UTF-8" };
  if (/loca\.lt$/i.test(base)) headers["bypass-tunnel-reminder"] = "1";

  var ctrl = typeof AbortController === "function" ? new AbortController() : null;
  var timer = setTimeout(function () { ctrl && ctrl.abort(); }, 20000);
  var promise = fetch(base + "/api/bootstrap", {
    method: "POST", headers: headers, body: JSON.stringify(body), signal: ctrl ? ctrl.signal : undefined,
  }).then(function (r) { clearTimeout(timer); return r; }, function (e) { clearTimeout(timer); throw e; });
  promise.catch(function () { /* обработает app.js */ });

  window.AKVAREL_BOOT = { platform: platform, initData: initData, body: body, promise: promise, at: Date.now() };
})();
