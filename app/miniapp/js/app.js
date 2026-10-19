/* Акварель — мини-приложение (клиент + кабинет мастера). Зависит от core.js и bridge.js. */
"use strict";

// =====================================================================
// Состояние
// =====================================================================
const S = {
  me: null,
  shop: null,
  services: [],
  masters: [],
  catalogAt: 0,
  route: "home", // home | mine | book | staff | sched | onboarding | blocked
  fatal: null,
  onboarding: { name: "", error: "" },
  profile: { name: "", error: "" },
  mine: { items: null, loading: true, error: null, tab: "up", req: 0 },
  book: null,
  staff: { day: null, masterId: null, items: null, av: {}, loading: false, error: null, req: 0, sig: "" },
  sched: { masterId: null, day: null, av: null, dayCount: null, week: null, loading: false, error: null, req: 0 },
  inv: null,
};

const isStaff = () => !!S.me && (S.me.role === "staff" || S.me.role === "admin");
const today = () => ymdOf(new Date());
const serviceById = (id) => S.services.find((s) => s.id === id);
const masterById = (id) => S.masters.find((m) => m.id === id);
const store = {
  get(k) { try { return localStorage.getItem("ak_" + k) || ""; } catch (_) { return ""; } },
  set(k, v) { try { localStorage.setItem("ak_" + k, v); } catch (_) { /* приватный режим */ } },
};

const STATUS_TEXT = {
  pending: "Ожидает", confirmed: "Подтверждена", completed: "Состоялась", cancelled: "Отменена", no_show: "Не пришёл",
};

// =====================================================================
// Запуск и авторизация
// =====================================================================
function authBody() {
  const body = { platform: Bridge.platform || "telegram", init_data: Bridge.initData };
  if (!Bridge.platform) {
    // Только для DEV_MODE: проверка в обычном браузере
    const q = new URLSearchParams(location.search);
    const staff = q.has("staff");
    body.user_id = Number(q.get("uid")) || (staff ? 9001 : 1001);
    // Клиенту имя не подставляем — спросим в онбординге
    if (staff) body.full_name = q.get("name") || "Мастер";
    else if (q.get("name")) body.full_name = q.get("name");
    if (q.get("platform") === "max") body.platform = "max";
  }
  return body;
}

async function authenticate() {
  const res = await api("/api/auth", { method: "POST", body: authBody() });
  Net.token = res.token;
  S.me = res;
}
Net.reauth = authenticate;

async function loadCatalog(force = false) {
  if (!force && S.services.length && Date.now() - S.catalogAt < 5 * 60 * 1000) return;
  const [services, masters] = await Promise.all([api("/api/services"), api("/api/masters")]);
  S.services = services;
  S.masters = masters;
  S.catalogAt = Date.now();
}

/** Применяет ответ /api/bootstrap: салон, профиль, каталог и стартовые данные экрана. */
function applyBootstrap(data) {
  S.shop = data.shop;
  TZ = S.shop.timezone;
  Net.token = data.me.token;
  S.me = data.me;
  if (data.services && data.services.length) {
    S.services = data.services;
    S.masters = data.masters || [];
    S.catalogAt = Date.now();
  }
  if (Array.isArray(data.mine)) {
    S.mine.items = data.mine; S.mine.loading = false; S.mine.error = null;
  }
  if (data.staff_day) applyStaffDay(data.staff_day);
}

/** Ответ раннего запроса из prefetch.js → данные (или ошибка в формате ApiError). */
async function prefetched() {
  const pre = window.AKVAREL_BOOT;
  if (!pre || !pre.promise) return null;
  let res;
  try { res = await pre.promise; } catch (_) { return null; } // сеть/таймаут — повторим обычным путём
  let data = null;
  try { data = await res.json(); } catch (_) { /* не JSON */ }
  if (!res.ok) throw normalizeError(res.status, data);
  if (!data || !data.me) return null;
  return data;
}

// ---- кэш первого экрана: при повторном открытии рисуем интерфейс сразу, не дожидаясь сети
const BOOT_CACHE_V = 1;

/** Ключ кэша по пользователю (id из initData, без запроса к серверу) — чужие данные не покажем. */
function bootCacheKey() {
  const body = (window.AKVAREL_BOOT && window.AKVAREL_BOOT.body) || authBody();
  let id = "";
  try {
    const p = new URLSearchParams(Bridge.initData || "");
    const u = p.get("user");
    id = u ? String(JSON.parse(u).id || JSON.parse(u).user_id || "") : String(p.get("user_id") || "");
  } catch (_) { /* нет initData */ }
  if (!Bridge.platform) id = String(body.user_id || "");
  return id ? `boot:${body.platform}:${id}` : "";
}
function readBootCache(key) {
  if (!key) return null;
  try {
    const d = JSON.parse(store.get(key) || "null");
    return d && d.v === BOOT_CACHE_V && d.me && d.shop && d.services ? d : null;
  } catch (_) { return null; }
}
function writeBootCache(key, data) {
  if (!key || !data || !data.me) return;
  const { shop, me, services, masters } = data;
  store.set(key, JSON.stringify({ v: BOOT_CACHE_V, shop, me, services, masters, at: Date.now() }));
}

/** Стартовый экран по роли и hash (#/mine, #/sched, #/book). */
function routeInitial({ fetchLists }) {
  const h = Bridge.hashPath();
  if (isStaff()) {
    S.route = h === "sched" ? "sched" : "staff";
    if (!S.staff.day) S.staff.day = today();
    render();
    if (S.route === "sched") loadSched();
    else if (fetchLists && !S.staff.items) loadStaffDay();
  } else {
    S.route = h === "mine" ? "mine" : "home";
    render();
    if (fetchLists && !S.mine.items) loadMine();
    if (h === "book") startBooking({});
  }
}

async function boot() {
  // Первый запрос уже ушёл из <head> (prefetch.js) — initData известен из URL, SDK грузится параллельно.
  const pre = window.AKVAREL_BOOT;
  const sent = pre && pre.body ? pre.body : authBody();
  let req = pre && pre.promise
    ? prefetched().then((d) => d || api("/api/bootstrap", { method: "POST", body: sent }))
    : api("/api/bootstrap", { method: "POST", body: sent });

  // Повторное открытие: показываем интерфейс из кэша мгновенно, списки подъедут с ответом сервера
  const cacheKey = bootCacheKey();
  const cached = readBootCache(cacheKey);
  let fromCache = false;
  if (cached && !cached.me.is_blocked && !cached.me.needs_name) {
    applyBootstrap({ ...cached, mine: null, staff_day: null });
    fromCache = true;
    routeInitial({ fetchLists: false });
  }

  await Bridge.ready;
  Bridge.init();
  if (fromCache) syncChrome();
  const now = authBody();
  if (now.init_data !== sent.init_data || now.platform !== sent.platform) {
    // SDK дал другой initData, чем мы прочитали из URL — перезапрашиваем с ним
    req.catch(() => {});
    req = api("/api/bootstrap", { method: "POST", body: now });
  }

  let data;
  try {
    data = await req;
  } catch (e) {
    if (!fromCache) { S.fatal = e; render(); return; }
    // Интерфейс уже показан из кэша — покажем ошибку в списках, не роняя экран
    S.mine.loading = false; S.mine.error = e.message;
    S.staff.loading = false; S.staff.error = e.message;
    render();
    return;
  }
  writeBootCache(cacheKey, data);
  applyBootstrap(data);

  if (S.me.is_blocked && !isStaff()) {
    S.route = "blocked";
    render();
    return;
  }
  if (!isStaff() && S.me.needs_name) {
    S.onboarding.name = "";
    S.onboarding.error = "";
    S.route = "onboarding";
    render();
    return;
  }
  if (!S.services.length) {
    try { await loadCatalog(true); } catch (e) { S.fatal = e; render(); return; }
  }

  if (fromCache) {
    // Роль могла поменяться (/role в боте) — тогда экран другой; иначе просто обновляем данные
    const staffScreen = S.route === "staff" || S.route === "sched";
    if (isStaff() !== staffScreen) { S.book = null; routeInitial({ fetchLists: true }); return; }
    if (S.route !== "book") render();
    return;
  }
  routeInitial({ fetchLists: true });
}

// =====================================================================
// Рендер и «хром» приложения
// =====================================================================
let lastKey = "";
function screenKey() {
  const b = S.book;
  if (S.route === "book" && b) return `book:${b.mode}:${b.step}:${b.done ? 1 : 0}`;
  if (S.route === "onboarding" || S.route === "blocked") return S.route;
  return S.route + (S.route === "mine" ? ":" + S.mine.tab : "");
}

function render() {
  const app = $("#app");
  const key = screenKey();
  const same = key === lastKey;
  const y = same ? window.scrollY : 0;
  const strips = same ? [...app.querySelectorAll(".days, .chips")].map((e) => e.scrollLeft) : [];
  app.innerHTML = str(view());
  lastKey = key;
  window.scrollTo(0, y);
  app.querySelectorAll(".days, .chips").forEach((e, i) => {
    if (same && strips[i] !== undefined) e.scrollLeft = strips[i];
    else {
      const a = e.querySelector(".active");
      if (a) e.scrollLeft = Math.max(0, a.offsetLeft - e.clientWidth / 2 + a.offsetWidth / 2);
    }
  });
  syncChrome();
  if (S.route === "onboarding") {
    const el = $("#f-oname");
    if (el && document.activeElement !== el) setTimeout(() => el.focus(), 80);
  }
}

function view() {
  if (S.fatal) return fatalView();
  switch (S.route) {
    case "blocked": return blockedView();
    case "onboarding": return onboardingView();
    case "home": return homeView();
    case "mine": return mineView();
    case "book": return bookView();
    case "staff": return staffView();
    case "sched": return schedView();
    default: return homeView();
  }
}

function currentBack() {
  if (S.fatal || S.route === "onboarding" || S.route === "blocked") return null;
  if (S.route === "book" && S.book) return bookBack;
  return null;
}

function syncChrome() {
  const b = S.book;
  Bridge.setBack(currentBack());
  Bridge.confirmClose(S.route === "book" && !!b && !b.done && (b.mode === "resched" || b.step >= 3));
}

function loadingScreen() {
  return html`<div class="screen"><div class="hero"><div class="brand">Акварель</div></div>
    <div class="sk block"></div><div class="sk block"></div><div class="sk block"></div></div>`;
}

function fatalView() {
  const e = S.fatal;
  const offline = e && e.code === "network";
  return html`<div class="fatal">
    <div class="brand">Акварель</div>
    <div class="empty"><div class="ico">${icon(offline ? "refresh" : "info", "lg")}</div>
      <h3>${offline ? "Нет связи" : "Не удалось открыть"}</h3>
      <p>${e && e.message ? e.message : "Попробуйте ещё раз"}</p>
      <button class="btn primary" data-act="reload">Повторить</button></div></div>`;
}

function blockedView() {
  const shop = S.shop || {};
  return html`<div class="fatal">
    <div class="brand">${shop.name || "Акварель"}</div>
    <div class="empty"><div class="ico">${icon("alert", "lg")}</div>
      <h3>Доступ ограничен</h3>
      <p>Запись через приложение недоступна. Если это ошибка — позвоните в салон.</p>
      ${shop.phone ? html`<a class="btn primary" href="${telHref(shop.phone)}">Позвонить</a>` : ""}</div></div>`;
}

function onboardingView() {
  const o = S.onboarding;
  const shop = S.shop || {};
  return html`<div class="screen no-tabbar" style="padding-top:0">
    <div class="hero"><div class="hello">Добро пожаловать</div>
      <div class="brand">${shop.name || "Акварель"}</div>
      <p style="margin-top:12px;opacity:.85;font-size:14.5px">Как к вам обращаться? Имя увидит мастер при записи.</p></div>
    <div class="field ${o.error ? "err" : ""}" style="margin-top:8px">
      <label for="f-oname">Ваше имя</label>
      <input class="input" id="f-oname" data-in="oname" value="${o.name}" autocomplete="name" maxlength="40" placeholder="Например: Анна">
      <div class="msg">${o.error || ""}</div>
      <div class="help">Потом имя можно изменить на главном экране</div></div>
    <div class="cta-bar"><button class="btn primary block" data-act="onboard-save">Продолжить</button></div></div>`;
}

function topbar(title, { back = false, side = "" } = {}) {
  const showBack = back && !Bridge.hasNativeBack;
  return html`<header class="topbar">
    ${showBack ? html`<button class="back" data-act="back" aria-label="Назад">${icon("back")}</button>` : html`<span class="pad"></span>`}
    <h1>${title}</h1>${side ? html`<span class="side">${side}</span>` : ""}</header>`;
}

function tabbar() {
  const staff = isStaff();
  const tabs = staff
    ? [["staff", "Записи", "calendar"], ["sched", "График", "sliders"]]
    : [["home", "Главная", "home"], ["mine", "Мои записи", "list"]];
  const n = staff ? 0 : upcoming().length;
  return html`<nav class="tabbar">${tabs.map(([r, label, ic]) => html`
    <button class="tab ${S.route === r ? "active" : ""}" data-act="tab" data-r="${r}">
      ${icon(ic)}<span>${label}</span>${r === "mine" && n ? html`<i class="dot">${n}</i>` : ""}
    </button>`)}</nav>`;
}

function errorBlock(message, act = "retry") {
  return html`<div class="empty"><div class="ico">${icon("refresh", "lg")}</div>
    <h3>Не получилось загрузить</h3><p>${message}</p>
    <button class="btn primary" data-act="${act}">Повторить</button></div>`;
}
function emptyBlock(ic, title, text, btn) {
  return html`<div class="empty"><div class="ico">${icon(ic, "lg")}</div><h3>${title}</h3>${text ? html`<p>${text}</p>` : ""}${btn || ""}</div>`;
}
const skeleton = (n = 3) => html`${Array.from({ length: n }, () => html`<div class="sk block"></div>`)}`;

// =====================================================================
// Клиент: главная
// =====================================================================
const upcoming = () =>
  (S.mine.items || []).filter((b) => b.is_upcoming).sort((a, b) => a.starts_at.localeCompare(b.starts_at));
const history = () =>
  (S.mine.items || []).filter((b) => !b.is_upcoming).sort((a, b) => b.starts_at.localeCompare(a.starts_at));

async function loadMine(silent = false) {
  const m = S.mine;
  const req = ++m.req;
  if (!silent && !m.items) { m.loading = true; m.error = null; }
  try {
    const items = await api("/api/bookings/mine");
    if (req !== m.req) return;
    m.items = items; m.loading = false; m.error = null;
  } catch (e) {
    if (req !== m.req) return;
    m.loading = false;
    if (!m.items) m.error = e.message;
  }
  if (S.route === "home" || S.route === "mine") render();
}

function homeView() {
  const shop = S.shop;
  const first = (S.me.full_name || "").split(" ")[0];
  const up = upcoming();
  const last = history().find((b) => b.status === "completed");
  const groups = [];
  S.services.forEach((s) => { if (!groups.find((g) => g.category === s.category)) groups.push(s); });

  const hero = html`<div class="hero">
    <div class="row between" style="align-items:flex-start">
      <div class="grow"><div class="hello">${first ? `Здравствуйте, ${first}` : "Добро пожаловать"}</div>
        <div class="brand">${shop.name}</div></div>
      <button class="btn sm" style="background:rgba(255,255,255,.16);color:#fff;margin-top:4px" data-act="profile-open" aria-label="Сменить имя">${icon("user", "sm")}</button>
    </div>
    ${shop.address || shop.phone ? html`<div class="contacts">
      ${shop.address ? html`<span>${icon("pin", "sm")}${shop.address}</span>` : ""}
      ${shop.phone ? html`<a href="${telHref(shop.phone)}">${icon("phone", "sm")}${shop.phone}</a>` : ""}</div>` : ""}
  </div>`;

  const listLoading = S.mine.loading && !S.mine.items;
  const body = html`
    ${listLoading ? html`<div class="sk block" style="height:120px"></div>` : ""}
    ${up.length ? html`
      <div class="section-title"><span>Ближайшая запись</span>${up.length > 1 ? html`<button class="linkbtn" data-act="tab" data-r="mine">Все (${up.length})</button>` : ""}</div>
      ${bookingCard(up[0], { next: true })}` : ""}
    <button class="btn primary block" style="height:56px;font-size:17px;margin:${up.length ? 4 : 0}px 0 6px" data-act="book-new">
      ${icon("calendar")}${up.length ? "Записаться ещё" : "Записаться"}</button>
    ${last ? html`
      <div class="list mt-12"><button class="item" data-act="repeat" data-id="${last.id}">
        <span class="avatar sm any">${icon("repeat", "sm")}</span>
        <span class="grow"><div class="title">Повторить прошлый визит</div>
          <div class="sub">${last.service_name} · ${last.master_name}</div></span>${icon("chevR", "chev")}</button></div>` : ""}
    <div class="section-title"><span>Быстрая запись</span></div>
    <div class="list">${groups.slice(0, 6).map((s) => html`
      <button class="item" data-act="quick" data-id="${s.id}">
        <span class="grow"><div class="title">${s.name}</div><div class="sub">${s.category} · ${duration(s.duration_minutes)}</div></span>
        <span class="price">${money(s.price_rub)}</span></button>`)}</div>
    <p class="small muted" style="text-align:center;margin:14px 8px 0">
      Отменить или перенести запись можно не позднее чем за ${shop.cancel_min_hours} ч до визита.</p>`;

  return html`<div class="screen">${hero}${body}</div>${tabbar()}`;
}

// ---- карточка записи клиента
function paymentNote(b) {
  const p = b.payment;
  if (!p) return "";
  if (p.status === "paid") return html`<span class="badge ok">${icon("check", "sm")}Оплачено</span>`;
  if (p.status === "expired") return "";
  return html`<span class="badge warn">Ждёт оплаты · ${money(p.amount_rub)}</span>`;
}

function bookingCard(b, { next = false } = {}) {
  const t = today();
  const ymd = ymdOf(b.starts_at);
  const live = b.payment && ["pending", "sent"].includes(b.payment.status);
  const cancelled = b.status === "cancelled";
  return html`<article class="bk ${next ? "next" : ""} ${b.is_upcoming ? "" : "past"} ${cancelled ? "cancelled" : ""}">
    <div class="row between top">
      <div class="when"><span class="d">${dayTitle(ymd, t)}</span></div>
      ${b.is_upcoming ? "" : html`<span class="badge ${cancelled || b.status === "no_show" ? "danger" : ""}">${["pending", "confirmed"].includes(b.status) ? "Состоялась" : STATUS_TEXT[b.status] || b.status}</span>`}
    </div>
    <div class="when" style="margin-top:2px"><span class="t">${timeOf(b.starts_at)}–${timeOf(b.ends_at)}</span></div>
    <div class="what">${b.service_name}</div>
    <div class="meta">Мастер ${b.master_name} · ${duration(b.duration_minutes)} · ${money(b.price_rub)}</div>
    ${b.payment && b.payment.status === "paid" ? html`<div style="margin-top:10px">${paymentNote(b)}</div>` : ""}
    ${live ? html`<div class="note">${icon("qr", "sm")}<span>Счёт на ${money(b.payment.amount_rub)} отправлен вам в чат — оплатите по QR-коду.</span></div>` : ""}
    ${b.is_upcoming && b.can_modify ? html`<div class="actions">
        <button class="btn" data-act="resched" data-id="${b.id}">${icon("edit", "sm")}Перенести</button>
        <button class="btn" data-act="cancel" data-id="${b.id}">Отменить</button></div>` : ""}
    ${b.is_upcoming && !b.can_modify ? html`<div class="note">${icon("info", "sm")}<span>Онлайн изменить запись уже нельзя (менее ${S.shop.cancel_min_hours} ч до визита).
        ${S.shop.phone ? html`<a href="${telHref(S.shop.phone)}" style="text-decoration:underline"> Позвоните нам</a>` : ""}</span></div>` : ""}
    ${!b.is_upcoming ? html`<div class="actions"><button class="btn primary" data-act="repeat" data-id="${b.id}">${icon("repeat", "sm")}Записаться снова</button></div>` : ""}
  </article>`;
}

// =====================================================================
// Клиент: мои записи
// =====================================================================
function mineView() {
  const m = S.mine;
  const up = upcoming(), past = history();
  const list = m.tab === "up" ? up : past;
  let body;
  if (m.loading && !m.items) body = skeleton(3);
  else if (m.error && !m.items) body = errorBlock(m.error, "mine-retry");
  else if (!list.length) {
    body = m.tab === "up"
      ? emptyBlock("calendar", "Предстоящих записей нет", "Выберите услугу и удобное время — это займёт минуту.",
          html`<button class="btn primary" data-act="book-new">Записаться</button>`)
      : emptyBlock("list", "История пока пуста", "Здесь будут ваши прошлые визиты.");
  } else body = html`${list.map((b) => bookingCard(b))}`;
  return html`<div class="screen">${topbar("Мои записи")}
    <div class="seg"><button class="${m.tab === "up" ? "active" : ""}" data-act="mine-tab" data-t="up">Предстоящие${up.length ? ` · ${up.length}` : ""}</button>
    <button class="${m.tab === "past" ? "active" : ""}" data-act="mine-tab" data-t="past">История</button></div>
    ${body}</div>${tabbar()}`;
}

// =====================================================================
// Мастер записи: шаги
// =====================================================================
function newBook(init = {}) {
  return {
    mode: "new", step: 1, serviceId: null, masterId: null, day: null, slot: null,
    days: null, slots: null, daysLoading: false, slotsLoading: false, daysError: null, slotsError: null,
    next: null, nextLoading: false, nextError: null,
    name: store.get("name") || (S.me && S.me.full_name) || "",
    phone: store.get("phone") || (S.me && S.me.phone) || "",
    comment: "", errors: {}, done: null, origin: S.route === "book" ? "home" : S.route,
    booking: null, staff: false, dReq: 0, sReq: 0, nReq: 0, ...init,
  };
}

async function startBooking({ serviceId = null, masterId = null } = {}) {
  S.book = newBook({ serviceId, masterId, step: serviceId ? 2 : 1 });
  S.route = "book";
  render();
  try { await loadCatalog(); } catch (_) { /* останется кэш */ }
  if (S.book && S.book.step === 1) render();
  if (serviceId) loadNext();
}

function leaveBook() {
  const origin = (S.book && S.book.origin) || (isStaff() ? "staff" : "home");
  S.book = null;
  S.route = origin === "book" ? "home" : origin;
  render();
  if (isStaff()) loadStaffDay({ silent: true }); else loadMine(true);
}

function bookBack() {
  const b = S.book;
  if (!b) return;
  if (b.done) return leaveBook();
  if (b.mode === "resched") return leaveBook();
  if (b.step > 1) {
    b.step -= 1;
    render();
    if (b.step === 2 && !b.next) loadNext();
  } else leaveBook();
}

async function loadNext() {
  const b = S.book;
  if (!b) return;
  const req = ++b.nReq;
  b.nextLoading = true; b.nextError = null; b.next = null;
  render();
  try {
    const res = await api("/api/masters/next", { query: { service_id: b.serviceId } });
    if (S.book !== b || req !== b.nReq) return;
    b.next = res;
  } catch (e) {
    if (S.book !== b || req !== b.nReq) return;
    b.nextError = e.message;
  }
  b.nextLoading = false;
  if (S.route === "book") render();
}

async function loadDays() {
  const b = S.book;
  if (!b) return;
  const req = ++b.dReq;
  b.daysLoading = true; b.daysError = null; b.days = null; b.slots = null;
  render();
  try {
    const days = await api("/api/availability/days", {
      query: { service_id: b.serviceId, master_id: b.masterId, exclude_booking_id: b.booking ? b.booking.id : null },
    });
    if (S.book !== b || req !== b.dReq) return;
    b.days = days;
    const keep = b.day && days.find((d) => d.day === b.day && d.free > 0);
    b.day = keep ? b.day : (days.find((d) => d.free > 0) || {}).day || null;
  } catch (e) {
    if (S.book !== b || req !== b.dReq) return;
    b.daysError = e.message;
  }
  b.daysLoading = false;
  if (S.route === "book") render();
  if (b.day && !b.daysError) loadSlots();
}

async function loadSlots() {
  const b = S.book;
  if (!b || !b.day) return;
  const req = ++b.sReq;
  b.slotsLoading = true; b.slotsError = null; b.slots = null; b.slot = null;
  render();
  try {
    const slots = await api("/api/slots", {
      query: { service_id: b.serviceId, day: b.day, master_id: b.masterId, exclude_booking_id: b.booking ? b.booking.id : null },
    });
    if (S.book !== b || req !== b.sReq) return;
    b.slots = slots;
  } catch (e) {
    if (S.book !== b || req !== b.sReq) return;
    b.slotsError = e.message;
  }
  b.slotsLoading = false;
  if (S.route === "book") render();
}

function bookView() {
  const b = S.book;
  if (!b) return homeView();
  if (b.done) return successView();
  const resched = b.mode === "resched";
  const title = resched ? (b.staff ? "Перенос записи" : "Перенос записи") : "Запись";
  const steps = resched ? "" : html`<div class="steps">${[1, 2, 3, 4].map((i) => html`<i class="${i < b.step ? "done" : i === b.step ? "now" : ""}"></i>`)}</div>`;
  let inner;
  if (b.step === 1) inner = stepService(b);
  else if (b.step === 2) inner = stepMaster(b);
  else if (b.step === 3) inner = stepTime(b);
  else inner = stepConfirm(b);
  return html`<div class="screen no-tabbar">${topbar(title, { back: true, side: resched ? "" : `Шаг ${b.step} из 4` })}${steps}${inner}</div>${bookCta(b)}`;
}

function serviceSummary(b, changeStep = 1) {
  const s = serviceById(b.serviceId);
  if (!s) return "";
  return html`<div class="card flat row" style="padding:12px 14px">
    <span class="grow"><div class="strong">${s.name}</div><div class="small muted">${duration(s.duration_minutes)} · ${money(s.price_rub)}</div></span>
    ${b.mode === "new" ? html`<button class="linkbtn" data-act="b-step" data-s="${changeStep}">Изменить</button>` : ""}</div>`;
}

// ---- шаг 1: услуга
function stepService(b) {
  if (!S.services.length) return skeleton(5);
  const cats = [];
  S.services.forEach((s) => { if (!cats.includes(s.category)) cats.push(s.category); });
  return html`<h2 class="step-title">Выберите услугу</h2>
    <p class="step-sub">Время и мастера — на следующих шагах</p>
    <div class="chips sticky">${cats.map((c, i) => html`<button class="chip" data-act="cat" data-i="${i}">${c}</button>`)}</div>
    ${cats.map((c, i) => html`
      <div class="section-title cat-h" id="cat-${i}"><span>${c}</span></div>
      <div class="list">${S.services.filter((s) => s.category === c).map((s) => html`
        <button class="item" data-act="b-service" data-id="${s.id}">
          <span class="grow"><div class="title">${s.name}</div>
            <div class="sub">${s.description ? s.description + " · " : ""}${duration(s.duration_minutes)}</div></span>
          <span class="price">${money(s.price_rub)}</span></button>`)}</div>`)}`;
}

// ---- шаг 2: мастер
function nextLabel(iso) { return iso ? whenText(iso, today()) : null; }

function stepMaster(b) {
  let list;
  if (b.nextLoading || (!b.next && !b.nextError)) list = skeleton(4);
  else if (b.nextError) list = errorBlock(b.nextError, "next-retry");
  else {
    const any = b.next.any;
    const items = S.masters.map((m) => {
      const n = b.next.masters.find((x) => x.master_id === m.id);
      return { m, at: n && n.starts_at };
    });
    if (!any) {
      list = emptyBlock("calendar", "Нет свободного времени", `В ближайшие ${S.shop.horizon_days} дн. все окна заняты. Позвоните нам — подскажем варианты.`,
        S.shop.phone ? html`<a class="btn primary" href="${telHref(S.shop.phone)}">Позвонить</a>` : "");
    } else {
      list = html`<div class="list">
        <button class="item" data-act="b-master" data-id="any">
          <span class="avatar any">${icon("user")}</span>
          <span class="grow"><div class="title">Любой мастер</div><div class="sub">Ближайшее: ${nextLabel(any)}</div></span>${icon("chevR", "chev")}</button>
        ${items.map(({ m, at }) => html`
          <button class="item ${at ? "" : "disabled"}" data-act="b-master" data-id="${m.id}" ${at ? "" : "disabled"}>
            <span class="avatar">${(m.name || "?")[0]}</span>
            <span class="grow"><div class="title">${m.name}</div>
              <div class="sub">${m.title ? m.title + " · " : ""}${at ? "ближайшее: " + nextLabel(at) : "нет мест в ближайшие дни"}</div></span>${icon("chevR", "chev")}</button>`)}
      </div>`;
    }
  }
  return html`<h2 class="step-title">Выберите мастера</h2>
    <p class="step-sub">Если не важно — выберите «Любой», так запишем на самое раннее время</p>
    ${serviceSummary(b, 1)}${list}`;
}

// ---- шаг 3: дата и время
function dayBtn(ymd, { active, free = null, act, today: t }) {
  const d = plain(ymd);
  return html`<button class="day ${active ? "active" : ""} ${free === 0 ? "off" : ""} ${isWeekend(ymd) ? "weekend" : ""} ${ymd === t ? "today" : ""}"
    data-act="${act}" data-d="${ymd}">
    <div class="wd">${WD_SHORT.format(d)}</div><div class="dn">${DAY_NUM.format(d)}</div>
    <div class="fr">${free > 0 && free <= 3 ? "мало" : ""}</div></button>`;
}

function partOfDay(label) {
  const h = Number(label.slice(0, 2));
  return h < 12 ? "Утро" : h < 17 ? "День" : "Вечер";
}

function stepTime(b) {
  const resched = b.mode === "resched";
  const t = today();
  const parts = [];
  if (resched) {
    parts.push(html`<div class="card tint small" style="padding:12px 14px"><b>Сейчас:</b> ${b.booking.service_name}, ${whenText(b.booking.starts_at, t)}
      ${b.staff ? html`<br><b>Клиент:</b> ${b.booking.client_name}` : html`<br><b>Мастер:</b> ${b.booking.master_name}`}</div>`);
    if (b.staff) {
      parts.push(html`<div class="chips">${S.masters.map((m) => html`<button class="chip ${b.masterId === m.id ? "active" : ""}" data-act="b-pick-master" data-id="${m.id}">${m.name}</button>`)}</div>`);
    }
  } else {
    parts.push(html`<h2 class="step-title">Дата и время</h2>`, serviceSummary(b, 1));
    parts.push(html`<div class="small muted" style="margin:0 4px 10px">Мастер: <b style="color:var(--ink)">${b.masterId ? (masterById(b.masterId) || {}).name : "любой свободный"}</b>
      <button class="linkbtn" style="margin-left:6px" data-act="b-step" data-s="2">изменить</button></div>`);
  }

  if (b.daysLoading || (!b.days && !b.daysError)) { parts.push(skeleton(2)); return html`${parts}`; }
  if (b.daysError) { parts.push(errorBlock(b.daysError, "days-retry")); return html`${parts}`; }
  if (!b.day) {
    parts.push(emptyBlock("calendar", "Нет свободного времени", `В ближайшие ${S.shop.horizon_days} дн. всё занято.`,
      S.shop.phone ? html`<a class="btn primary" href="${telHref(S.shop.phone)}">Позвонить в салон</a>` : ""));
    return html`${parts}`;
  }

  parts.push(html`<div class="month">${monthTitle(b.day)}</div>
    <div class="days">${b.days.map((d) => dayBtn(d.day, { active: d.day === b.day, free: d.free, act: "b-day", today: t }))}</div>`);

  if (b.slotsLoading || (!b.slots && !b.slotsError)) parts.push(skeleton(2));
  else if (b.slotsError) parts.push(errorBlock(b.slotsError, "slots-retry"));
  else if (!b.slots.length) parts.push(emptyBlock("clock", "На этот день мест нет", "Выберите другую дату"));
  else {
    const groups = {};
    b.slots.forEach((s) => (groups[partOfDay(s.label)] = groups[partOfDay(s.label)] || []).push(s));
    parts.push(html`${["Утро", "День", "Вечер"].filter((p) => groups[p]).map((p) => html`
      <div class="part">${p}</div>
      <div class="slots">${groups[p].map((s) => html`<button class="slot ${b.slot && b.slot.starts_at === s.starts_at ? "active" : ""}" data-act="b-slot" data-t="${s.starts_at}">${s.label}</button>`)}</div>`)}`);
  }
  return html`${parts}`;
}

// ---- шаг 4: подтверждение
function stepConfirm(b) {
  const s = serviceById(b.serviceId);
  const m = b.masterId ? masterById(b.masterId) : null;
  const t = today();
  const f = (k) => b.errors[k] ? "err" : "";
  return html`<h2 class="step-title">Проверьте и запишитесь</h2>
    <div class="card kv-wrap mt-12"><dl class="kv">
      <dt>Услуга</dt><dd>${s.name}</dd>
      <dt>Мастер</dt><dd>${m ? m.name : "любой свободный"}</dd>
      <dt>Когда</dt><dd>${dayTitle(b.day, t)}<br>${b.slot.label}–${timeOf(b.slot.ends_at)}</dd>
      <dt>Стоимость</dt><dd>${money(s.price_rub)}</dd></dl>
      <div class="divider"></div>
      <div class="row between"><button class="linkbtn" data-act="b-step" data-s="3">${icon("clock", "sm")} Другое время</button>
      <button class="linkbtn" data-act="b-step" data-s="2">Другой мастер</button></div></div>
    <div class="section-title"><span>Ваши данные</span></div>
    <div class="field ${f("name")}"><label for="f-name">Имя</label>
      <input class="input" id="f-name" data-in="name" value="${b.name}" autocomplete="name" maxlength="60" placeholder="Как к вам обращаться">
      <div class="msg">${b.errors.name || ""}</div></div>
    <div class="field ${f("phone")}"><label for="f-phone">Телефон</label>
      <input class="input" id="f-phone" data-in="phone" type="tel" inputmode="tel" value="${maskPhone(b.phone)}" autocomplete="tel" placeholder="+7 (900) 000-00-00">
      <div class="msg">${b.errors.phone || ""}</div>
      <div class="help">Позвоним, только если что-то изменится</div></div>
    <div class="field"><label for="f-comment">Комментарий (необязательно)</label>
      <textarea class="textarea" id="f-comment" data-in="comment" maxlength="500" placeholder="Например: длина волос, пожелания">${b.comment}</textarea></div>
    <div class="card tint small">${icon("info", "sm")} Подтверждение придёт в чат с ботом. Бесплатно отменить или перенести запись можно за ${S.shop.cancel_min_hours} ч и более до визита.</div>`;
}

function bookCta(b) {
  if (b.mode === "resched") {
    const ok = !!b.slot;
    return html`<div class="cta-bar">${ok ? html`<div class="hint">${dayTitle(b.day, today())} · ${b.slot.label}</div>` : ""}
      <button class="btn primary block" data-act="resched-go" ${ok ? "" : "disabled"}>Перенести запись</button></div>`;
  }
  if (b.step === 3) {
    const ok = !!b.slot;
    return html`<div class="cta-bar">${ok ? html`<div class="hint">${dayTitle(b.day, today())} · ${b.slot.label}</div>` : html`<div class="hint">Выберите время</div>`}
      <button class="btn primary block" data-act="b-next" ${ok ? "" : "disabled"}>Далее</button></div>`;
  }
  if (b.step === 4) {
    return html`<div class="cta-bar"><button class="btn primary block" data-act="b-submit">Записаться</button></div>`;
  }
  return "";
}

function successView() {
  const r = S.book.done;
  const t = today();
  const shop = S.shop;
  return html`<div class="screen no-tabbar">
    <div class="success"><div class="check">${icon("check")}</div>
      <h2>Вы записаны!</h2><p class="muted mt-8">Подтверждение отправили в чат с ботом</p></div>
    <div class="card mt-16"><div class="when"><span class="d serif" style="font-size:22px">${dayTitle(ymdOf(r.starts_at), t)}</span></div>
      <div class="strong" style="color:var(--brown-600);margin-top:2px">${timeOf(r.starts_at)}–${timeOf(r.ends_at)}</div>
      <div class="divider"></div>
      <dl class="kv"><dt>Услуга</dt><dd>${r.service_name}</dd><dt>Мастер</dt><dd>${r.master_name}</dd>
      <dt>Стоимость</dt><dd>${money(r.price_rub)}</dd>
      ${shop.address ? html`<dt>Адрес</dt><dd>${shop.address}</dd>` : ""}</dl></div>
    <div class="card tint small">${icon("info", "sm")} Перенести или отменить запись можно в разделе «Мои записи» не позднее чем за ${shop.cancel_min_hours} ч до визита.</div></div>
    <div class="cta-bar"><div class="btn-row"><button class="btn" data-act="done-home">На главную</button>
      <button class="btn primary" data-act="done-mine">Мои записи</button></div></div>`;
}

// =====================================================================
// Кабинет мастера: записи дня
// =====================================================================
function staffMasters() { return S.staff.masterId ? [masterById(S.staff.masterId)].filter(Boolean) : S.masters; }

/** Кладёт ответ /api/staff/day-full в состояние. Возвращает true, если данные изменились. */
function applyStaffDay(data) {
  const st = S.staff;
  const av = {};
  Object.values(data.availability || {}).forEach((a) => (av[a.master_id] = a));
  const sig = JSON.stringify([data.items, av, data.day, data.master_id || null]);
  const changed = sig !== st.sig;
  st.day = data.day;
  st.masterId = data.master_id || null;
  st.items = data.items; st.av = av; st.sig = sig; st.loading = false; st.error = null;
  return changed;
}

async function loadStaffDay({ silent = false } = {}) {
  const st = S.staff;
  if (!st.day) st.day = today();
  const req = ++st.req;
  if (!silent) { st.loading = true; st.error = null; if (S.route === "staff") render(); }
  try {
    // Записи дня + доступность всех мастеров — одним запросом
    const data = await api("/api/staff/day-full", { query: { day: st.day, master_id: st.masterId } });
    if (req !== st.req) return;
    if (data.day !== st.day || (data.master_id || null) !== (st.masterId || null)) return;
    const changed = applyStaffDay(data);
    if (!changed && silent) return;
  } catch (e) {
    if (req !== st.req) return;
    st.loading = false;
    if (!silent) st.error = e.message;
    else return;
  }
  if (S.route === "staff") render();
}

function staffView() {
  const st = S.staff;
  const t = today();
  const hero = html`<div class="hero mini"><div class="brand">${S.shop.name}</div><span class="role">Кабинет мастера</span></div>`;
  const rel = relDay(st.day, t);
  const d = plain(st.day);
  const bar = html`<div class="daybar">
    <button class="nav" data-act="sday" data-n="-1" aria-label="Предыдущий день">${icon("chevL")}</button>
    <div class="label"><div class="big">${rel || WD_LONG.format(d)}</div>
      <div class="sm">${rel ? WD_LONG.format(d) + ", " : ""}${DAY_MONTH.format(d)}</div>
      <input class="dateinput" type="date" value="${st.day}" data-in="sdate" aria-label="Выбрать дату"></div>
    <button class="nav" data-act="sday" data-n="1" aria-label="Следующий день">${icon("chevR")}</button></div>
    ${st.day !== t ? html`<div style="text-align:center;margin:-4px 0 10px"><button class="linkbtn" data-act="stoday">К сегодняшнему дню</button></div>` : ""}
    <div class="chips"><button class="chip ${st.masterId ? "" : "active"}" data-act="smaster" data-id="">Все мастера</button>
      ${S.masters.map((m) => html`<button class="chip ${st.masterId === m.id ? "active" : ""}" data-act="smaster" data-id="${m.id}">${m.name}</button>`)}</div>`;

  let body;
  if (st.error && !st.items) body = errorBlock(st.error, "staff-retry");
  else if (!st.items) body = skeleton(3);
  else body = staffTimeline();
  return html`<div class="screen" style="padding-top:0">${hero}${bar}${body}</div>${tabbar()}`;
}

function staffTimeline() {
  const st = S.staff;
  const items = st.items || [];
  const showMaster = !st.masterId;
  const active = items.filter((b) => b.status !== "no_show");
  const sum = active.reduce((a, b) => a + b.price_rub, 0);
  const rows = [];
  items.forEach((b) => rows.push({ t: timeOf(b.starts_at), kind: 1, b }));
  const banners = [];
  staffMasters().forEach((m) => {
    const av = st.av[m.id];
    if (!av) return;
    if (av.is_day_off) banners.push({ m, av });
    (av.breaks || []).forEach((br) => rows.push({ t: br.start, kind: 0, br, m }));
  });
  rows.sort((a, b) => a.t.localeCompare(b.t) || a.kind - b.kind);

  return html`
    <div class="summary"><div class="box"><b>${items.length}</b><span>${plural(items.length, "запись", "записи", "записей")}</span></div>
      <div class="box"><b>${sum ? money(sum) : "0\u00A0₽"}</b><span>на сумму</span></div></div>
    ${banners.map(({ m, av }) => html`<div class="offbanner">${icon("moon", "sm")}<span>${m.name} — выходной${av.reason ? ` (${av.reason})` : av.weekly_off ? " по графику" : ""}</span></div>`)}
    ${rows.length ? rows.map((r) => r.kind ? html`
      <div class="tl"><div class="time">${r.t}<small>${duration(r.b.duration_minutes)}</small></div><div class="body">${staffCard(r.b, showMaster)}</div></div>` : html`
      <div class="tl"><div class="time" style="padding-top:10px">${r.t}</div><div class="body"><div class="brk">${icon("coffee", "sm")}<span>${r.br.reason || "Перерыв"} до ${r.br.end}${showMaster ? ` · ${r.m.name}` : ""}</span></div></div></div>`)
      : banners.length ? "" : emptyBlock("calendar", "На этот день записей нет", "Новые записи появятся здесь сами")}`;
}

function staffCard(b, showMaster) {
  const p = b.payment;
  const paid = p && p.status === "paid";
  const live = p && ["pending", "sent"].includes(p.status);
  const cls = paid ? "paid" : b.status === "completed" ? "done" : b.status === "no_show" ? "noshow" : "";
  const needsMark = b.status !== "completed" && b.status !== "no_show" && !b.is_upcoming;
  return html`<article class="bk ${cls}">
    <div class="row between top"><div class="who">${b.client_name}</div>
      <div class="row gap-4" style="flex-wrap:wrap;justify-content:flex-end">
        ${b.status === "completed" ? html`<span class="badge">Состоялась</span>` : ""}
        ${b.status === "no_show" ? html`<span class="badge danger">Не пришёл</span>` : ""}
        ${needsMark ? html`<span class="badge warn">Отметьте итог</span>` : ""}
        ${paid ? html`<span class="badge ok">${icon("check", "sm")}${money(p.amount_rub)}</span>` : ""}
        ${live ? html`<span class="badge warn">Счёт ${money(p.amount_rub)}</span>` : ""}
      </div></div>
    <div class="what" style="font-weight:600">${b.service_name}</div>
    <div class="meta">${timeOf(b.starts_at)}–${timeOf(b.ends_at)}${showMaster ? ` · ${b.master_name}` : ""} · ${money(b.price_rub)}</div>
    <a class="meta" href="${telHref(b.client_phone)}" style="display:inline-flex;gap:6px;align-items:center;margin-top:4px;color:var(--brown-500);font-weight:700">${icon("phone", "sm")}${maskPhone(b.client_phone)}</a>
    ${b.comment ? html`<div class="meta" style="margin-top:4px;font-style:italic">«${b.comment}»</div>` : ""}
    <div class="actions">
      ${paid ? "" : live
        ? html`<button class="btn primary" data-act="pay-show" data-id="${b.id}">${icon("qr", "sm")}Показать QR</button>`
        : html`<button class="btn primary" data-act="pay-new" data-id="${b.id}">${icon("qr", "sm")}Выставить счёт</button>`}
      <button class="btn" style="${paid ? "" : "flex:none;width:52px;padding:0"}" data-act="smenu" data-id="${b.id}" aria-label="Ещё">${paid ? "Действия" : icon("more")}</button>
    </div></article>`;
}

// =====================================================================
// Кабинет мастера: график
// =====================================================================
async function loadSched({ silent = false } = {}) {
  const sc = S.sched;
  if (!sc.masterId && S.masters.length) sc.masterId = S.masters[0].id;
  if (!sc.day) sc.day = today();
  if (!sc.masterId) return;
  const req = ++sc.req;
  if (!silent) { sc.loading = true; sc.error = null; if (S.route === "sched") render(); }
  try {
    const [av, dayItems, week] = await Promise.all([
      api("/api/staff/availability", { query: { master_id: sc.masterId, day: sc.day } }),
      api("/api/staff/day", { query: { day: sc.day, master_id: sc.masterId } }),
      api(`/api/staff/schedule/${sc.masterId}`),
    ]);
    if (req !== sc.req) return;
    sc.av = av; sc.dayCount = dayItems.length; sc.week = week; sc.loading = false; sc.error = null;
  } catch (e) {
    if (req !== sc.req) return;
    sc.loading = false; sc.error = e.message;
  }
  if (S.route === "sched") render();
}

const WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

function schedView() {
  const sc = S.sched;
  const t = today();
  if (!sc.day) sc.day = t;
  if (!sc.masterId && S.masters.length) sc.masterId = S.masters[0].id;
  const master = masterById(sc.masterId);
  const days = Array.from({ length: 35 }, (_, i) => addDays(t, i));
  let body;
  if (sc.error && !sc.av) body = errorBlock(sc.error, "sched-retry");
  else if (!sc.av) body = skeleton(3);
  else body = schedBody(master);
  return html`<div class="screen">${topbar("График работы")}
    <div class="chips">${S.masters.map((m) => html`<button class="chip ${sc.masterId === m.id ? "active" : ""}" data-act="sched-master" data-id="${m.id}">${m.name}</button>`)}</div>
    <div class="month">${monthTitle(sc.day)}</div>
    <div class="days">${days.map((d) => dayBtn(d, { active: d === sc.day, act: "sched-day", today: t }))}</div>
    ${body}</div>${tabbar()}`;
}

function schedBody(master) {
  const sc = S.sched, av = sc.av, t = today();
  const dayTxt = dayTitle(sc.day, t);
  let card;
  if (av.day_off_set) {
    card = html`<div class="offbanner">${icon("moon", "sm")}<span>Выходной${av.reason ? ` · ${av.reason}` : ""}</span></div>
      <button class="btn block" data-act="dayoff-clear">Сделать рабочим днём</button>`;
  } else if (av.weekly_off) {
    card = html`<div class="offbanner">${icon("moon", "sm")}<span>Выходной по недельному графику</span></div>
      <p class="small muted">Чтобы работать по таким дням, измените недельный график ниже.</p>`;
  } else {
    card = html`<div class="row between"><span class="muted">Рабочее время</span><b>${av.work_start}–${av.work_end}</b></div>
      ${sc.dayCount ? html`<div class="row between mt-8"><span class="muted">Записей на день</span><b>${sc.dayCount}</b></div>` : ""}
      <div class="section-title" style="margin-top:16px"><span>Перерывы</span></div>
      ${av.breaks.length ? av.breaks.map((b) => html`<div class="brk" style="margin-bottom:8px">${icon("coffee", "sm")}<span class="grow">${b.start}–${b.end}${b.reason ? ` · ${b.reason}` : ""}</span>
        <button class="back" style="width:32px;height:32px" data-act="break-del" data-id="${b.id}" aria-label="Удалить перерыв">${icon("x", "sm")}</button></div>`)
        : html`<p class="small muted" style="margin-bottom:8px">Перерывов нет — клиенты видят всё рабочее время.</p>`}
      <div class="btn-row mt-12"><button class="btn" data-act="break-add">${icon("coffee", "sm")}Перерыв</button>
        <button class="btn" data-act="dayoff-set">${icon("moon", "sm")}Выходной</button></div>`;
  }
  const week = sc.week ? sc.week.days : [];
  return html`<div class="card"><h3 style="margin-bottom:10px">${dayTxt} · ${master ? master.name : ""}</h3>${card}</div>
    <div class="card"><div class="row between"><h3>Недельный график</h3><button class="linkbtn" data-act="week-edit">Изменить</button></div>
      <div style="margin-top:8px">${week.map((d) => html`<div class="row between small" style="padding:5px 0"><span class="strong">${WD[d.weekday]}</span>
        <span class="${d.is_day_off ? "muted" : ""}">${d.is_day_off ? "выходной" : `${d.start}–${d.end}`}</span></div>`)}</div></div>`;
}

// =====================================================================
// Счета и QR
// =====================================================================
function findStaffBooking(id) { return (S.staff.items || []).find((b) => b.id === Number(id)); }

function openInvoice(id, replace = false) {
  const b = findStaffBooking(id);
  if (!b) return;
  const ready = S.shop.payments_ready;
  const item = Sheets.open(() => html`
    <h2>Счёт на оплату</h2>
    <div class="text"><b>${b.client_name}</b> · ${b.service_name}<br><span class="small muted">По прайсу: ${money(b.price_rub)}</span></div>
    ${ready ? "" : html`<div class="card tint small" style="background:var(--warn-bg);color:var(--warn)">${icon("alert", "sm")} Реквизиты для оплаты не заполнены на сервере (PAY_* в .env). QR выставить нельзя.</div>`}
    <div class="field"><label for="amount">Сумма к оплате</label>
      <div class="amount"><input class="input" id="amount" data-in="amount" type="text" inputmode="numeric" value="${b.price_rub}" autocomplete="off"></div>
      <div class="help">Можно изменить, если цена отличается от прайса</div></div>
    <div class="btn-row v"><button class="btn primary" data-act="inv-send" ${ready ? "" : "disabled"}>${icon("send", "sm")}Отправить QR клиенту</button>
      <button class="btn" data-act="_no">Отмена</button></div>`);
  item.no = () => Sheets.close(item);
  S.inv = { id: b.id, replace, sheet: item };
  setTimeout(() => { const a = $("#amount"); if (a) { a.focus(); a.select(); } }, 250);
}

async function sendInvoice(btn, skipConfirm = false) {
  const inv = S.inv;
  const b = inv && findStaffBooking(inv.id);
  if (!b) return;
  const amount = parseInt(($("#amount") || {}).value, 10);
  if (!(amount > 0)) return toast("Введите сумму больше нуля", "err");
  const ok = skipConfirm || await askConfirm({
    title: "Отправить счёт клиенту?",
    text: html`<b>${b.client_name}</b> получит QR-код на оплату <b>${money(amount)}</b> за «${b.service_name}».`,
    confirm: "Отправить QR", cancel: "Назад",
  });
  if (!ok) return;
  const r = await busy(btn, async () => {
    try {
      const p = await api("/api/staff/payments", { method: "POST", body: { booking_id: b.id, amount_rub: amount, replace: inv.replace } });
      Sheets.close(inv.sheet);
      Bridge.haptic("success");
      showQr(p, b);
      loadStaffDay({ silent: true });
    } catch (e) {
      if (e.code === "payment_exists") {
        const again = await askConfirm({
          title: "Счёт уже выставлен",
          text: html`По этой записи уже есть неоплаченный счёт на <b>${money(e.detail.amount_rub || 0)}</b>. Выставить новый? Прежний QR перестанет действовать.`,
          confirm: "Выставить новый", cancel: "Оставить прежний",
        });
        if (again) { inv.replace = true; return "retry"; }
      } else if (e.code === "already_paid") {
        Sheets.close(inv.sheet);
        toast("Эта запись уже оплачена", "ok");
        loadStaffDay({ silent: true });
      } else {
        toast(e.message, "err");
      }
    }
  });
  if (r === "retry") return sendInvoice(btn, true);
}

function showQr(p, b) {
  const item = Sheets.open(() => html`
    <h2 style="text-align:center">${money(p.amount_rub)}</h2>
    <div class="text" style="text-align:center">${(b && b.client_name) || p.client_name || ""}${p.description ? html`<br><span class="small muted">${p.description}</span>` : ""}</div>
    ${p.qr_url ? html`<img class="qr" src="${mediaUrl(p.qr_url)}" alt="QR-код для оплаты">` : ""}
    ${p.delivered === true ? html`<div class="card tint small" style="background:var(--ok-bg);color:var(--ok)">${icon("check", "sm")} QR отправлен клиенту в чат</div>` : ""}
    ${p.delivered === false ? html`<div class="card tint small" style="background:var(--warn-bg);color:var(--warn)">${icon("alert", "sm")} Не удалось отправить в чат (клиент мог не запускать бота). Покажите QR с экрана.</div>` : ""}
    <div class="btn-row v">
      ${p.status === "paid" ? "" : html`<button class="btn primary" data-act="pay-paid" data-id="${p.id}" data-b="${b ? b.id : ""}">${icon("check", "sm")}Клиент оплатил</button>
      <button class="btn" data-act="pay-redo" data-b="${b ? b.id : ""}">Изменить сумму / выставить заново</button>`}
      <button class="btn ghost" data-act="_no">Закрыть</button></div>`);
  item.no = () => Sheets.close(item);
  S.qrSheet = item;
}

// =====================================================================
// Действия
// =====================================================================
const actions = {
  _yes() { const t = Sheets.top; if (t && t.yes) t.yes(); },
  _no() { const t = Sheets.top; if (!t) return; if (t.no) t.no(); else Sheets.close(t); },
  reload() { location.reload(); },
  back() { const fn = currentBack(); if (fn) fn(); },

  async "onboard-save"(d, btn) {
    const name = (S.onboarding.name || "").trim();
    if (name.length < 2) {
      S.onboarding.error = "Укажите имя (минимум 2 буквы)";
      render();
      Bridge.haptic("error");
      return;
    }
    await busy(btn, async () => {
      try {
        const res = await api("/api/me", { method: "PATCH", body: { full_name: name } });
        Net.token = res.token;
        S.me = res;
        store.set("name", res.full_name || name);
        writeBootCache(bootCacheKey(), { shop: S.shop, me: S.me, services: S.services, masters: S.masters });
        Bridge.haptic("success");
        if (!S.services.length) await loadCatalog(true);
        S.route = "home";
        render();
        loadMine();
      } catch (e) {
        if (e.code === "blocked") { S.route = "blocked"; render(); return; }
        S.onboarding.error = e.message;
        render();
      }
    });
  },
  "profile-open"() {
    S.profile.name = S.me.full_name || "";
    S.profile.error = "";
    const item = Sheets.open(() => html`
      <h2>Ваше имя</h2>
      <div class="text">Так вас видит мастер в записи. Можно изменить в любой момент.</div>
      <div class="field ${S.profile.error ? "err" : ""}">
        <label for="f-pname">Имя</label>
        <input class="input" id="f-pname" data-in="pname" value="${S.profile.name}" autocomplete="name" maxlength="40">
        <div class="msg">${S.profile.error || ""}</div></div>
      <div class="btn-row v">
        <button class="btn primary" data-act="profile-save">Сохранить</button>
        <button class="btn" data-act="_no">Отмена</button></div>`);
    item.no = () => Sheets.close(item);
    S.profileSheet = item;
    setTimeout(() => { const el = $("#f-pname"); if (el) { el.focus(); el.select(); } }, 200);
  },
  async "profile-save"(d, btn) {
    const name = (S.profile.name || "").trim();
    if (name.length < 2) {
      S.profile.error = "Укажите имя (минимум 2 буквы)";
      if (S.profileSheet) S.profileSheet.draw();
      return;
    }
    await busy(btn, async () => {
      try {
        const res = await api("/api/me", { method: "PATCH", body: { full_name: name } });
        Net.token = res.token;
        S.me = res;
        store.set("name", res.full_name || name);
        writeBootCache(bootCacheKey(), { shop: S.shop, me: S.me, services: S.services, masters: S.masters });
        if (S.book) S.book.name = res.full_name || name;
        Sheets.close(S.profileSheet);
        Bridge.haptic("success");
        toast("Имя сохранено", "ok");
        render();
      } catch (e) {
        S.profile.error = e.message;
        if (S.profileSheet) S.profileSheet.draw();
      }
    });
  },

  tab(d) {
    if (S.route === d.r) { window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    S.route = d.r;
    render();
    if (d.r === "mine") loadMine(true);
    if (d.r === "staff") loadStaffDay({ silent: !!S.staff.items });
    if (d.r === "sched") loadSched({ silent: !!S.sched.av });
  },

  // --- клиент
  "book-new": () => startBooking({}),
  quick: (d) => startBooking({ serviceId: Number(d.id) }),
  repeat(d) {
    const b = (S.mine.items || []).find((x) => x.id === Number(d.id));
    if (!b) return;
    startBooking({ serviceId: b.service_id });
  },
  "mine-tab": (d) => { S.mine.tab = d.t; render(); },
  "mine-retry": () => loadMine(),
  retry: () => loadMine(),

  async cancel(d, btn) {
    const b = (S.mine.items || []).find((x) => x.id === Number(d.id));
    if (!b) return;
    const t = today();
    const ok = await askConfirm({
      title: "Отменить запись?",
      text: html`<b>${b.service_name}</b><br>${dayTitle(ymdOf(b.starts_at), t)}, ${timeOf(b.starts_at)} · мастер ${b.master_name}<br><br>Время освободится для других клиентов.`,
      confirm: "Да, отменить", cancel: "Оставить запись", danger: true,
    });
    if (!ok) return;
    await busy(btn, async () => {
      try {
        await api(`/api/bookings/${b.id}/cancel`, { method: "POST" });
        Bridge.haptic("warning");
        toast("Запись отменена");
        await loadMine(true);
      } catch (e) {
        await showInfo({ title: "Не удалось отменить", text: e.message });
        loadMine(true);
      }
    });
  },
  resched(d) {
    const b = (S.mine.items || []).find((x) => x.id === Number(d.id));
    if (!b) return;
    S.book = newBook({ mode: "resched", booking: b, serviceId: b.service_id, masterId: b.master_id, step: 3, origin: S.route });
    S.route = "book";
    loadDays();
  },

  // --- мастер записи
  cat(d) { const el = $("#cat-" + d.i); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); },
  "b-service"(d) {
    const b = S.book;
    b.serviceId = Number(d.id); b.masterId = null; b.day = null; b.slot = null; b.step = 2; b.next = null;
    Bridge.haptic("select");
    loadNext();
  },
  "b-master"(d) {
    const b = S.book;
    b.masterId = d.id === "any" ? null : Number(d.id); b.day = null; b.slot = null; b.step = 3; b.days = null; b.slots = null;
    Bridge.haptic("select");
    loadDays();
  },
  "b-pick-master"(d) {
    const b = S.book;
    if (b.masterId === Number(d.id)) return;
    b.masterId = Number(d.id); b.day = null; b.slot = null;
    loadDays();
  },
  "b-day"(d) { const b = S.book; if (b.day === d.d) return; b.day = d.d; Bridge.haptic("select"); loadSlots(); },
  "b-slot"(d) {
    const b = S.book;
    b.slot = b.slots.find((s) => s.starts_at === d.t) || null;
    Bridge.haptic("select");
    render();
  },
  "b-step"(d) {
    const b = S.book;
    b.step = Number(d.s);
    if (b.step === 2 && !b.next) loadNext(); else render();
  },
  "b-next"() { const b = S.book; if (!b.slot) return; b.step = 4; render(); },
  "next-retry": () => loadNext(),
  "days-retry": () => loadDays(),
  "slots-retry": () => loadSlots(),
  "b-submit": (d, btn) => submitBooking(btn),
  "resched-go": (d, btn) => doResched(btn),
  "done-home"() { S.book = null; S.route = "home"; render(); },
  "done-mine"() { S.book = null; S.route = "mine"; S.mine.tab = "up"; render(); },

  // --- кабинет мастера
  sday(d) { shiftStaffDay(Number(d.n)); },
  stoday() { S.staff.day = today(); S.staff.items = null; loadStaffDay(); },
  smaster(d) { S.staff.masterId = d.id ? Number(d.id) : null; S.staff.items = null; loadStaffDay(); },
  "staff-retry": () => loadStaffDay(),

  smenu(d) { openStaffMenu(Number(d.id)); },
  "pay-new"(d) { openInvoice(d.id); },
  async "pay-show"(d, btn) {
    const b = findStaffBooking(d.id);
    if (!b || !b.payment) return;
    await busy(btn, async () => {
      try { showQr(await api(`/api/staff/payments/${b.payment.id}`), b); }
      catch (e) { toast(e.message, "err"); }
    });
  },
  "inv-send": (d, btn) => sendInvoice(btn),
  "pay-redo"(d) {
    Sheets.close(S.qrSheet);
    const b = findStaffBooking(d.b);
    if (b) openInvoice(b.id, true);
  },
  async "pay-paid"(d, btn) {
    const b = findStaffBooking(d.b);
    const ok = await askConfirm({
      title: "Оплата получена?",
      text: html`Отметить счёт${b ? html` для <b>${b.client_name}</b>` : ""} как оплаченный. Запись будет отмечена как состоявшаяся.`,
      confirm: "Да, оплачено", cancel: "Назад",
    });
    if (!ok) return;
    await busy(btn, async () => {
      try {
        await api(`/api/staff/payments/${d.id}/paid`, { method: "POST" });
        Sheets.close(S.qrSheet);
        Bridge.haptic("success");
        toast("Оплата отмечена", "ok");
        loadStaffDay({ silent: true });
      } catch (e) { toast(e.message, "err"); }
    });
  },

  "sm-resched"(d) {
    Sheets.closeAll();
    const b = findStaffBooking(d.id);
    if (!b) return;
    S.book = newBook({ mode: "resched", staff: true, booking: b, serviceId: b.service_id, masterId: b.master_id, step: 3, origin: "staff" });
    S.route = "book";
    loadDays();
  },
  async "sm-status"(d) {
    Sheets.closeAll();
    try {
      await api(`/api/staff/bookings/${d.id}/status`, { method: "POST", body: { status: d.s } });
      toast(d.s === "completed" ? "Отмечено: визит состоялся" : d.s === "no_show" ? "Отмечено: клиент не пришёл" : "Статус возвращён");
      loadStaffDay({ silent: true });
    } catch (e) { toast(e.message, "err"); }
  },
  async "sm-delete"(d) {
    const b = findStaffBooking(d.id);
    if (!b) return;
    Sheets.closeAll();
    const ok = await askConfirm({
      title: "Удалить запись?",
      text: html`<b>${b.client_name}</b> · ${b.service_name}<br>${dayTitle(ymdOf(b.starts_at), today())}, ${timeOf(b.starts_at)} · ${b.master_name}<br><br>
        Запись будет удалена без возможности восстановления.${b.is_upcoming ? " Клиент получит уведомление." : ""}`,
      confirm: "Удалить", cancel: "Не удалять", danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/staff/bookings/${b.id}`, { method: "DELETE" });
      Bridge.haptic("warning");
      toast("Запись удалена");
      loadStaffDay({ silent: true });
    } catch (e) { toast(e.message, "err"); }
  },
  async "sm-block"(d) {
    Sheets.closeAll();
    const name = d.name || "клиента";
    const ok = await askConfirm({
      title: "Заблокировать клиента?",
      text: html`<b>${name}</b> больше не сможет записываться через приложение.<br><br>
        <b>Все его записи будут удалены</b>, клиент получит уведомление.`,
      confirm: "Заблокировать", cancel: "Отмена", danger: true,
    });
    if (!ok) return;
    try {
      const res = await api(`/api/staff/clients/${d.id}/block`, { method: "POST" });
      Bridge.haptic("warning");
      toast(res.deleted_bookings
        ? `Клиент заблокирован, удалено записей: ${res.deleted_bookings}`
        : "Клиент заблокирован");
      loadStaffDay({ silent: true });
    } catch (e) { toast(e.message, "err"); }
  },

  // --- график
  "sched-master"(d) { S.sched.masterId = Number(d.id); S.sched.av = null; loadSched(); },
  "sched-day"(d) { S.sched.day = d.d; S.sched.av = null; loadSched(); },
  "sched-retry": () => loadSched(),
  "break-add": () => openBreakSheet(),
  "break-preset"(d) { const s = $("#br-start"), e = $("#br-end"); if (s && e) { s.value = d.s; e.value = d.e; } },
  "break-save": (d, btn) => saveBreak(btn, false),
  async "break-del"(d) {
    const ok = await askConfirm({ title: "Удалить перерыв?", text: html`Это время снова станет доступно для записи клиентов.`, confirm: "Удалить", cancel: "Оставить", danger: true });
    if (!ok) return;
    try { await api(`/api/staff/breaks/${d.id}`, { method: "DELETE" }); toast("Перерыв удалён"); loadSched({ silent: true }); }
    catch (e) { toast(e.message, "err"); }
  },
  "dayoff-set": () => setDayOff(false),
  async "dayoff-clear"() {
    const sc = S.sched;
    const ok = await askConfirm({ title: "Сделать рабочим днём?", text: html`<b>${dayTitle(sc.day, today())}</b> снова станет доступен для записи.`, confirm: "Сделать рабочим", cancel: "Отмена" });
    if (!ok) return;
    try { await api("/api/staff/day-off", { method: "DELETE", query: { master_id: sc.masterId, day: sc.day } }); toast("День снова рабочий", "ok"); loadSched({ silent: true }); }
    catch (e) { toast(e.message, "err"); }
  },
  "week-edit": () => openWeekSheet(),
  "week-toggle"(d) { const w = S.weekDraft[Number(d.i)]; w.is_day_off = !w.is_day_off; S.weekSheet.draw(); },
  "week-save": (d, btn) => saveWeek(btn),
};

function shiftStaffDay(n) {
  S.staff.day = addDays(S.staff.day, n);
  S.staff.items = null;
  loadStaffDay();
}

function openStaffMenu(id) {
  const b = findStaffBooking(id);
  if (!b) return;
  const active = b.status === "pending" || b.status === "confirmed";
  const item = Sheets.open(() => html`
    <h2>${b.client_name}</h2>
    <div class="text small muted">${b.service_name} · ${timeOf(b.starts_at)}–${timeOf(b.ends_at)}</div>
    <a class="menu-item" href="${telHref(b.client_phone)}">${icon("phone")}Позвонить клиенту</a>
    ${active ? html`<button class="menu-item" data-act="sm-resched" data-id="${b.id}">${icon("edit")}Перенести на другое время</button>` : ""}
    ${active ? html`<button class="menu-item" data-act="sm-status" data-id="${b.id}" data-s="completed">${icon("check")}Визит состоялся</button>
      <button class="menu-item" data-act="sm-status" data-id="${b.id}" data-s="no_show">${icon("x")}Клиент не пришёл</button>` : html`
      <button class="menu-item" data-act="sm-status" data-id="${b.id}" data-s="confirmed">${icon("repeat")}Вернуть в «ожидается»</button>`}
    <button class="menu-item danger" data-act="sm-delete" data-id="${b.id}">${icon("trash")}Удалить запись</button>
    <button class="menu-item danger" data-act="sm-block" data-id="${b.client_user_id}" data-name="${b.client_name}">${icon("alert")}Заблокировать клиента</button>
    <button class="btn ghost block mt-8" data-act="_no">Закрыть</button>`);
  item.no = () => Sheets.close(item);
}

// ---- перерывы и выходные
function timeOptions(from, to, step, selected) {
  const out = [];
  for (let m = from * 60; m <= to * 60; m += step) {
    const v = `${pad2(Math.floor(m / 60))}:${pad2(m % 60)}`;
    out.push(html`<option value="${v}" ${v === selected ? "selected" : ""}>${v}</option>`);
  }
  return out;
}

function openBreakSheet() {
  const av = S.sched.av;
  if (!av || !av.work_start) return;
  const ws = Number(av.work_start.slice(0, 2)), we = Number(av.work_end.slice(0, 2)) + (av.work_end.slice(3) !== "00" ? 1 : 0);
  const startDef = ws <= 13 && we >= 14 ? "13:00" : av.work_start;
  const endDef = startDef === "13:00" ? "14:00" : `${pad2(Math.min(ws + 1, 23))}:00`;
  const item = Sheets.open(() => html`
    <h2>Добавить перерыв</h2>
    <div class="text small muted">${dayTitle(S.sched.day, today())} · ${(masterById(S.sched.masterId) || {}).name}</div>
    <div class="chips" style="margin-bottom:6px"><button class="chip sm" data-act="break-preset" data-s="13:00" data-e="14:00">Обед 13–14</button>
      <button class="chip sm" data-act="break-preset" data-s="12:00" data-e="13:00">Обед 12–13</button>
      <button class="chip sm" data-act="break-preset" data-s="15:00" data-e="15:30">Короткий 15:00</button></div>
    <div class="two"><div class="field"><label>С</label><select class="select" id="br-start">${timeOptions(Math.max(ws - 0, 0), we, 15, startDef)}</select></div>
      <div class="field"><label>До</label><select class="select" id="br-end">${timeOptions(Math.max(ws - 0, 0), we, 15, endDef)}</select></div></div>
    <div class="field"><label>Причина (необязательно)</label><input class="input" id="br-reason" maxlength="60" placeholder="Например: обед"></div>
    <div class="btn-row v"><button class="btn primary" data-act="break-save">Добавить перерыв</button><button class="btn" data-act="_no">Отмена</button></div>`);
  item.no = () => Sheets.close(item);
  S.breakSheet = item;
}

async function saveBreak(btn, force) {
  const sc = S.sched;
  const start = $("#br-start").value, end = $("#br-end").value;
  const reason = $("#br-reason") ? $("#br-reason").value.trim() : "";
  if (end <= start) return toast("Конец перерыва должен быть позже начала", "err");
  const r = await busy(btn, async () => {
    try {
      await api("/api/staff/breaks", { method: "POST", body: { master_id: sc.masterId, day: sc.day, start_time: start, end_time: end, reason: reason || null, force } });
      Sheets.close(S.breakSheet);
      toast("Перерыв добавлен", "ok");
      loadSched({ silent: true });
    } catch (e) {
      if (e.code === "has_bookings") {
        const ok = await askConfirm({
          title: "На это время есть записи",
          text: html`Уже записано клиентов: <b>${e.detail.count}</b>. Перерыв будет добавлен, но записи останутся — их нужно будет перенести вручную.`,
          confirm: "Всё равно добавить", cancel: "Не добавлять", danger: true,
        });
        if (ok) return "retry";
      } else toast(e.message, "err");
    }
  });
  if (r === "retry") return saveBreak(btn, true);
}

async function setDayOff(force) {
  const sc = S.sched;
  if (!force) {
    const ok = await askConfirm({
      title: "Сделать день выходным?",
      text: html`<b>${dayTitle(sc.day, today())}</b> у мастера <b>${(masterById(sc.masterId) || {}).name}</b> станет недоступен для записи.`,
      confirm: "Сделать выходным", cancel: "Отмена",
    });
    if (!ok) return;
  }
  try {
    await api("/api/staff/day-off", { method: "POST", body: { master_id: sc.masterId, day: sc.day, force } });
    toast("День отмечен выходным", "ok");
    loadSched({ silent: true });
  } catch (e) {
    if (e.code === "has_bookings") {
      const ok = await askConfirm({
        title: "На этот день есть записи",
        text: html`Записано клиентов: <b>${e.detail.count}</b>. День станет выходным, но записи останутся — их нужно перенести или отменить вручную.`,
        confirm: "Всё равно выходной", cancel: "Отмена", danger: true,
      });
      if (ok) return setDayOff(true);
    } else toast(e.message, "err");
  }
}

// ---- недельный график
function openWeekSheet() {
  const sc = S.sched;
  if (!sc.week) return;
  S.weekDraft = sc.week.days.map((d) => ({ ...d }));
  const item = Sheets.open(() => html`
    <h2>Недельный график</h2>
    <div class="text small muted">${(masterById(sc.masterId) || {}).name}. Действует на все недели.</div>
    ${S.weekDraft.map((d, i) => html`<div class="wk"><span class="nm">${WD[d.weekday]}</span>
      ${d.is_day_off ? html`<span class="off-label">Выходной</span>` : html`
        <select class="select" data-in="wk-start" data-i="${i}">${timeOptions(6, 23, 30, d.start)}</select><span class="dash">–</span>
        <select class="select" data-in="wk-end" data-i="${i}">${timeOptions(6, 23, 30, d.end)}</select>`}
      <button class="switch ${d.is_day_off ? "" : "on"}" data-act="week-toggle" data-i="${i}" aria-label="Рабочий день"></button></div>`)}
    <div class="btn-row v mt-12"><button class="btn primary" data-act="week-save">Сохранить график</button><button class="btn" data-act="_no">Отмена</button></div>`);
  item.no = () => Sheets.close(item);
  S.weekSheet = item;
}

async function saveWeek(btn) {
  const sc = S.sched;
  for (const d of S.weekDraft) {
    if (!d.is_day_off && d.end <= d.start) return toast(`${WD[d.weekday]}: конец дня должен быть позже начала`, "err");
  }
  await busy(btn, async () => {
    try {
      await api(`/api/staff/schedule/${sc.masterId}`, { method: "PUT", body: { days: S.weekDraft } });
      Sheets.close(S.weekSheet);
      toast("График сохранён", "ok");
      loadSched({ silent: true });
    } catch (e) { toast(e.message, "err"); }
  });
}

// ---- отправка записи / перенос
async function submitBooking(btn) {
  const b = S.book;
  const errors = {};
  if ((b.name || "").trim().length < 2) errors.name = "Укажите имя";
  if (!phoneValid(b.phone)) errors.phone = "Введите номер полностью: +7 и 10 цифр";
  b.errors = errors;
  if (Object.keys(errors).length) {
    render();
    Bridge.haptic("error");
    const el = $(".field.err .input");
    if (el) { el.focus(); el.scrollIntoView({ block: "center", behavior: "smooth" }); }
    return;
  }
  await busy(btn, async () => {
    try {
      const res = await api("/api/bookings", {
        method: "POST",
        body: {
          service_id: b.serviceId, master_id: b.masterId, starts_at: b.slot.starts_at,
          client_name: b.name.trim(), client_phone: maskPhone(b.phone), comment: (b.comment || "").trim() || null,
        },
      });
      store.set("name", b.name.trim());
      store.set("phone", maskPhone(b.phone));
      b.done = res;
      Bridge.haptic("success");
      render();
      loadMine(true);
    } catch (e) {
      Bridge.haptic("error");
      if (e.code === "network") return toast(e.message, "err");
      await showInfo({
        title: e.code === "slot_taken" ? "Это время уже занято" : "Не удалось записаться",
        text: e.code === "slot_taken" ? "Пока вы оформляли запись, время забрал другой клиент. Выберите другое." : e.message,
        button: ["slot_taken", "client_overlap"].includes(e.code) ? "Выбрать другое время" : "Понятно",
      });
      if (["slot_taken", "client_overlap", "too_far"].includes(e.code)) { b.step = 3; b.slot = null; loadDays(); }
    }
  });
}

async function doResched(btn) {
  const b = S.book, bk = b.booking, slot = b.slot;
  if (!slot) return;
  const t = today();
  const ok = await askConfirm({
    title: "Перенести запись?",
    text: html`<b>${bk.service_name}</b>${b.staff ? html` · ${bk.client_name}` : ""}<br>
      Сейчас: ${dayTitle(ymdOf(bk.starts_at), t)}, ${timeOf(bk.starts_at)}<br>
      Будет: <b>${dayTitle(b.day, t)}, ${slot.label}</b>${b.staff ? html` · ${(masterById(b.masterId) || {}).name}<br><br>Клиент получит уведомление.` : ""}`,
    confirm: "Да, перенести", cancel: "Не переносить",
  });
  if (!ok) return;
  await busy(btn, async () => {
    try {
      const url = b.staff ? `/api/staff/bookings/${bk.id}/reschedule` : `/api/bookings/${bk.id}/reschedule`;
      const body = { starts_at: slot.starts_at };
      if (b.staff) body.master_id = b.masterId;
      await api(url, { method: "POST", body });
      Bridge.haptic("success");
      toast(b.staff ? "Запись перенесена, клиент уведомлён" : "Запись перенесена", "ok");
      if (b.staff) S.staff.day = b.day;
      S.staff.items = b.staff ? null : S.staff.items;
      leaveBook();
    } catch (e) {
      if (e.code === "network") return toast(e.message, "err");
      await showInfo({ title: e.code === "slot_taken" ? "Это время уже занято" : "Не удалось перенести", text: e.message });
      if (e.code === "slot_taken") loadDays();
    }
  });
}

// =====================================================================
// Ввод, события, жизненный цикл
// =====================================================================
const inputs = {
  name: (v) => { S.book.name = v; },
  phone: (v, el) => { S.book.phone = v; const m = maskPhone(v); if (el.value !== m) el.value = m; },
  comment: (v) => { S.book.comment = v; },
  oname: (v) => { S.onboarding.name = v; S.onboarding.error = ""; },
  pname: (v) => { S.profile.name = v; S.profile.error = ""; },
  amount: (v, el) => { const c = v.replace(/\D/g, "").slice(0, 7); if (c !== v) el.value = c; },
  sdate: (v) => { if (/^\d{4}-\d{2}-\d{2}$/.test(v)) { S.staff.day = v; S.staff.items = null; loadStaffDay(); } },
  "wk-start": (v, el) => { S.weekDraft[Number(el.dataset.i)].start = v; },
  "wk-end": (v, el) => { S.weekDraft[Number(el.dataset.i)].end = v; },
};

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  const fn = actions[el.dataset.act];
  if (!fn) return;
  Promise.resolve()
    .then(() => fn(el.dataset, el, e))
    .catch((err) => { console.error(err); toast((err && err.message) || "Что-то пошло не так", "err"); });
});

["input", "change"].forEach((ev) =>
  document.addEventListener(ev, (e) => {
    const el = e.target;
    const key = el && el.dataset && el.dataset.in;
    if (!key || !inputs[key]) return;
    const changeOnly = el.tagName === "SELECT" || el.type === "date";
    if (changeOnly !== (ev === "change")) return;
    inputs[key](el.value, el);
  })
);

// фоновое обновление
document.addEventListener("visibilitychange", () => {
  if (document.hidden || S.fatal || !S.me) return;
  if (isStaff()) {
    if (S.route === "staff") loadStaffDay({ silent: true });
    if (S.route === "sched") loadSched({ silent: true });
  } else if (S.route === "home" || S.route === "mine") loadMine(true);
});
setInterval(() => {
  if (document.hidden || S.fatal || !S.me || Sheets.stack.length) return;
  if (isStaff() && S.route === "staff") loadStaffDay({ silent: true });
}, 45000);

// офлайн-индикатор
function netBanner() {
  let el = $("#offline");
  if (!navigator.onLine && !el) {
    el = document.createElement("div");
    el.id = "offline"; el.className = "offline"; el.textContent = "Нет подключения к интернету";
    document.body.appendChild(el);
  } else if (navigator.onLine && el) el.remove();
}
window.addEventListener("online", netBanner);
window.addEventListener("offline", netBanner);

window.addEventListener("unhandledrejection", (e) => { console.error(e.reason); });

boot();
