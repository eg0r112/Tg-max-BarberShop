/* Единый мост для Telegram WebApp и MAX WebApp. Без зависимостей от приложения.

   Платформа и initData определяются сразу по URL (#tgWebAppData / #WebAppData),
   а SDK нужной платформы подгружается лениво — параллельно с первым запросом к API.
   Так приложение не ждёт 120 КБ скрипта Telegram (и не грузит лишний SDK MAX). */
window.Bridge = (function () {
  const SDK = {
    telegram: "https://telegram.org/js/telegram-web-app.js",
    max: "https://st.max.ru/js/max-web-app.js",
  };
  const SDK_TIMEOUT = 7000;

  function safe(fn) {
    try { return fn(); } catch (_) { return undefined; }
  }
  // Платформа дописывает параметры к фрагменту приложения ("#/mine?tgWebAppData=…") — ищем при любом разделителе
  function hashParam(name) {
    const m = location.hash.match(new RegExp("(?:^#|[#?&])" + name + "=([^&#]*)"));
    if (!m) return "";
    try { return decodeURIComponent(m[1]); } catch (_) { return m[1]; }
  }
  /** Путь приложения из фрагмента: "#/mine?tgWebAppData=…" → "mine". */
  function hashPath() {
    const p = location.hash.replace(/^#\/?/, "").split(/[?#&]/)[0];
    return p.indexOf("=") >= 0 ? "" : p;
  }
  const ss = (key) => safe(() => sessionStorage.getItem(key)) || "";

  /** Определяем платформу до загрузки SDK: по hash или по sessionStorage (после перезагрузки). */
  function detect() {
    let d = hashParam("tgWebAppData");
    if (!d) d = safe(() => JSON.parse(ss("__telegram__initParams") || "{}").tgWebAppData) || "";
    if (d) return { platform: "telegram", initData: d };
    d = hashParam("WebAppData") || ss("WebAppData");
    if (d) return { platform: "max", initData: d };
    return { platform: null, initData: "" }; // обычный браузер (проверка в DEV_MODE)
  }

  // prefetch.js уже определил платформу (и отправил первый запрос) — используем тот же результат
  const pre = window.AKVAREL_BOOT;
  const det = pre && pre.platform !== undefined ? { platform: pre.platform, initData: pre.initData || "" } : detect();
  let native = null;

  function pick() {
    const tg = window.Telegram && window.Telegram.WebApp;
    const mx = window.WebApp;
    if (det.platform === "telegram" && tg) return tg;
    if (det.platform === "max" && mx) return mx;
    if (tg && tg.initData) { det.platform = "telegram"; return tg; }
    if (mx && mx.initData) { det.platform = "max"; return mx; }
    return null;
  }

  function loadSdk(url) {
    return new Promise((resolve) => {
      let done = false;
      const finish = () => { if (!done) { done = true; clearTimeout(timer); resolve(); } };
      const s = document.createElement("script");
      s.src = url;
      s.async = true;
      s.onload = finish;
      s.onerror = finish;
      const timer = setTimeout(finish, SDK_TIMEOUT);
      document.head.appendChild(s);
    });
  }

  // Платформа не распознана по URL (нестандартный формат фрагмента) — страховка: грузим SDK Telegram,
  // он сам разберёт initData. В обычном браузере это просто лишние 100 КБ, ничего не ломает.
  const sdkUrl = det.platform ? SDK[det.platform] : SDK.telegram;
  const ready = loadSdk(sdkUrl).then(() => {
    native = pick();
    if (native && native.initData) det.initData = native.initData;
  });

  // ---- нативная кнопка «назад»
  let backHandler = null;
  let backBound = false;
  const backBtn = () => native && native.BackButton;
  const hasNativeBack = () => !!(backBtn() && typeof backBtn().onClick === "function");

  function init() {
    if (!native) return;
    safe(() => native.ready());
    safe(() => native.expand && native.expand());
    if (det.platform === "telegram") {
      safe(() => native.setHeaderColor && native.setHeaderColor("#3d2416"));
      safe(() => native.setBackgroundColor && native.setBackgroundColor("#f6f0e8"));
      safe(() => native.setBottomBarColor && native.setBottomBarColor("#ffffff"));
      safe(() => native.disableVerticalSwipes && native.disableVerticalSwipes());
    }
    if (hasNativeBack() && !backBound) {
      backBound = true;
      safe(() => backBtn().onClick(() => backHandler && backHandler()));
    }
  }

  function user() {
    const u = native && native.initDataUnsafe && native.initDataUnsafe.user;
    if (!u) return null;
    return {
      id: u.id,
      name: [u.first_name, u.last_name].filter(Boolean).join(" "),
      first: u.first_name || "",
    };
  }

  function setBack(handler) {
    backHandler = handler || null;
    if (!hasNativeBack()) return;
    safe(() => (handler ? backBtn().show() : backBtn().hide()));
  }

  function confirmClose(on) {
    if (!native) return;
    safe(() => (on ? native.enableClosingConfirmation() : native.disableClosingConfirmation()));
  }

  function haptic(kind) {
    const h = native && native.HapticFeedback;
    if (!h) return;
    safe(() => {
      if (kind === "success" || kind === "error" || kind === "warning") h.notificationOccurred(kind);
      else if (kind === "select") h.selectionChanged();
      else h.impactOccurred(kind || "light");
    });
  }

  function openLink(url) {
    if (native && native.openLink && /^https?:/i.test(url)) return safe(() => native.openLink(url));
    window.open(url, "_blank");
  }

  function close() { safe(() => native && native.close()); }

  return {
    get platform() { return det.platform; },
    get initData() { return det.initData; },
    get hasNativeBack() { return hasNativeBack(); },
    hashPath,
    ready,
    init,
    user,
    setBack,
    confirmClose,
    haptic,
    openLink,
    close,
  };
})();
