/* Базовые утилиты: безопасный html, форматирование, иконки, API, шторки, тосты. */
"use strict";

// ---------- безопасная шаблонизация (экранирует всё, кроме raw/html) ----------
class Raw { constructor(s) { this.s = s; } }
const raw = (s) => new Raw(String(s));
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);
const str = (v) =>
  v instanceof Raw ? v.s
  : Array.isArray(v) ? v.map(str).join("")
  : v === null || v === undefined || v === false ? ""
  : esc(v);
const html = (strings, ...vals) =>
  new Raw(strings.reduce((acc, s, i) => acc + s + (i < vals.length ? str(vals[i]) : ""), ""));

const $ = (sel, root = document) => root.querySelector(sel);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- иконки ----------
const ICONS = {
  home: '<path d="M3 11l9-7.5L21 11"/><path d="M5 9.5V20h14V9.5"/><path d="M10 20v-6h4v6"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15.500" rx="3"/><path d="M8 3v4M16 3v4M3.500 10h17"/>',
  list: '<rect x="5" y="3.500" width="14" height="17" rx="3"/><path d="M9 9h6M9 13h6M9 17h3"/>',
  clock: '<circle cx="12" cy="12" r="8.500"/><path d="M12 7.500V12l3 2"/>',
  phone: '<path d="M5 4h4l2 5-2.500 1.500a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
  pin: '<path d="M12 21s7-6.200 7-11.500a7 7 0 0 0-14 0C5 14.800 12 21 12 21z"/><circle cx="12" cy="9.500" r="2.500"/>',
  chevR: '<path d="m9 5 7 7-7 7"/>',
  chevL: '<path d="m15 5-7 7 7 7"/>',
  back: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  check: '<path d="m5 12.500 4.500 4.500L19 7.500"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  more: '<circle cx="5" cy="12" r="1.300"/><circle cx="12" cy="12" r="1.300"/><circle cx="19" cy="12" r="1.300"/>',
  qr: '<rect x="3.500" y="3.500" width="7" height="7" rx="1.500"/><rect x="13.500" y="3.500" width="7" height="7" rx="1.500"/><rect x="3.500" y="13.500" width="7" height="7" rx="1.500"/><path d="M14 14h2v2h-2zM18 14h3M14 18h2M18 18v3M21 18v.01"/>',
  edit: '<path d="M4 20h4L19 9a2.800 2.800 0 0 0-4-4L4 16z"/><path d="m13.500 6.500 4 4"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6.500 7l1 13h9l1-13M10 11v6M14 11v6"/>',
  coffee: '<path d="M5 9h11v5a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5zM16 10h1.500a2.500 2.500 0 0 1 0 5H16M8 3v3M12 3v3"/>',
  moon: '<path d="M20 14.500A8 8 0 0 1 9.500 4 8.500 8.500 0 1 0 20 14.500z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.800v.01"/>',
  alert: '<path d="M12 4 2.500 20h19z"/><path d="M12 10v4.500M12 17.200v.01"/>',
  send: '<path d="M21 3 10 14M21 3l-7 18-4-7-7-4z"/>',
  repeat: '<path d="M17 3l3 3-3 3M4 11V9a3 3 0 0 1 3-3h13M7 21l-3-3 3-3M20 13v2a3 3 0 0 1-3 3H4"/>',
  scissors: '<circle cx="6" cy="6.500" r="2.500"/><circle cx="6" cy="17.500" r="2.500"/><path d="M8 8l12 9M8 16 20 7"/>',
  sliders: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 20c1-4 4-6 8-6s7 2 8 6"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14-4L4 9M4 4v5h5M4 13a8 8 0 0 0 14 4l2-2M20 20v-5h-5"/>',
};
const icon = (name, cls = "") =>
  raw(`<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`);

// ---------- форматирование (всё в часовом поясе салона) ----------
let TZ = "Europe/Moscow";
const _fmt = {};
function fmt(key, opts) {
  const k = key + TZ;
  return _fmt[k] || (_fmt[k] = new Intl.DateTimeFormat(key.startsWith("sv") ? "sv-SE" : "ru-RU", { timeZone: TZ, ...opts }));
}
const pad2 = (n) => String(n).padStart(2, "0");

/** Дата «ГГГГ-ММ-ДД» в часовом поясе салона для момента времени. */
const ymdOf = (iso) => fmt("sv", { year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(iso));
/** «ЧЧ:ММ» в часовом поясе салона. */
const timeOf = (iso) => fmt("time", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(iso));

const plain = (ymd) => new Date(ymd + "T12:00:00Z");
const _u = (opts) => new Intl.DateTimeFormat("ru-RU", { timeZone: "UTC", ...opts });
const WD_SHORT = _u({ weekday: "short" });
const WD_LONG = _u({ weekday: "long" });
const DAY_NUM = _u({ day: "numeric" });
const DAY_MONTH = _u({ day: "numeric", month: "long" });
const DAY_MON_SHORT = _u({ day: "numeric", month: "short" });
const MONTH_YEAR = _u({ month: "long", year: "numeric" });
const MONTH_ONLY = _u({ month: "long" });

const addDays = (ymd, n) => {
  const d = plain(ymd);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
const daysBetween = (a, b) => Math.round((plain(b) - plain(a)) / 864e5);
const isWeekend = (ymd) => [0, 6].includes(plain(ymd).getUTCDay());

function relDay(ymd, today) {
  const diff = daysBetween(today, ymd);
  if (diff === 0) return "Сегодня";
  if (diff === 1) return "Завтра";
  if (diff === -1) return "Вчера";
  return null;
}
/** «Сегодня, 14 сентября» / «Ср, 1 октября» */
function dayTitle(ymd, today) {
  const rel = relDay(ymd, today);
  const dm = DAY_MONTH.format(plain(ymd));
  return rel ? `${rel}, ${dm}` : `${cap(WD_SHORT.format(plain(ymd)))}, ${dm}`;
}
/** «Сегодня в 15:30» / «Ср, 1 окт, 15:30» */
function whenText(iso, today) {
  const ymd = ymdOf(iso);
  const rel = relDay(ymd, today);
  const t = timeOf(iso);
  if (rel) return `${rel}, ${t}`;
  return `${cap(WD_SHORT.format(plain(ymd)))}, ${DAY_MON_SHORT.format(plain(ymd))}, ${t}`;
}
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
/** «Октябрь 2026» (без «г.», которое даёт Intl) */
const monthTitle = (ymd) => cap(MONTH_YEAR.format(plain(ymd)).replace(/\s*г\.?$/i, ""));
const money = (n) => (n === 0 ? "Бесплатно" : `${Number(n).toLocaleString("ru-RU")}\u00A0₽`);
function duration(min) {
  const h = Math.floor(min / 60), m = min % 60;
  if (h && m) return `${h} ч ${m} мин`;
  return h ? `${h} ч` : `${m} мин`;
}
function plural(n, one, few, many) {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  return b === 1 ? one : many;
}

// ---------- телефон ----------
function phoneDigits(v) {
  let d = String(v || "").replace(/\D/g, "");
  if (d.startsWith("8")) d = "7" + d.slice(1);
  if (d.length && !d.startsWith("7")) d = "7" + d;
  return d.slice(0, 11);
}
function maskPhone(v) {
  const d = phoneDigits(v);
  if (!d) return "";
  let out = "+7";
  if (d.length > 1) out += " (" + d.slice(1, 4);
  if (d.length >= 4) out += ")";
  if (d.length > 4) out += " " + d.slice(4, 7);
  if (d.length > 7) out += "-" + d.slice(7, 9);
  if (d.length > 9) out += "-" + d.slice(9, 11);
  return out;
}
const phoneValid = (v) => phoneDigits(v).length === 11;
const telHref = (v) => "tel:+" + String(v || "").replace(/\D/g, "");

// ---------- API ----------
class ApiError extends Error {
  constructor(message, code, status, detail) {
    super(message);
    this.code = code || "error";
    this.status = status || 0;
    this.detail = detail || {};
  }
}
const Net = { token: null, reauth: null };

function apiBase() {
  const raw = (window.AKVAREL_CONFIG && window.AKVAREL_CONFIG.apiBase) || "";
  return String(raw).replace(/\/$/, "");
}

/** Абсолютный URL к API/медиа (для GitHub Pages и кросс-домена). */
function apiUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  return apiBase() + path;
}

function mediaUrl(path) {
  if (!path) return "";
  return apiUrl(path);
}

function normalizeError(status, data) {
  const d = data && data.detail;
  if (d && typeof d === "object" && !Array.isArray(d)) return new ApiError(d.message || "Ошибка запроса", d.code, status, d);
  if (typeof d === "string") return new ApiError(d, "error", status, {});
  if (Array.isArray(d)) return new ApiError("Проверьте введённые данные", "validation", status, {});
  return new ApiError(status >= 500 ? "Сервер временно недоступен. Повторите чуть позже." : "Ошибка запроса", "error", status, {});
}

async function api(path, { method = "GET", body, query } = {}) {
  let url = apiUrl(path);
  if (query) {
    const p = new URLSearchParams();
    Object.entries(query).forEach(([k, v]) => v !== null && v !== undefined && v !== "" && p.append(k, v));
    const qs = p.toString();
    if (qs) url += (url.includes("?") ? "&" : "?") + qs;
  }
  let reauthed = false;
  for (let attempt = 0; ; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 20000);
    let res;
    try {
      const headers = {
        "Content-Type": "application/json",
        ...(Net.token ? { Authorization: "Bearer " + Net.token } : {}),
      };
      // localtunnel без этого заголовка отдаёт страницу-заглушку вместо API
      if (/loca\.lt$/i.test(apiBase())) headers["bypass-tunnel-reminder"] = "1";
      res = await fetch(url, {
        method,
        signal: ctrl.signal,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (_) {
      clearTimeout(timer);
      if (method === "GET" && attempt < 1) { await sleep(700); continue; }
      throw new ApiError("Нет связи с сервером. Проверьте интернет и повторите.", "network", 0);
    }
    clearTimeout(timer);
    let data = null;
    try { data = await res.json(); } catch (_) { /* пустой ответ */ }
    if (res.status === 401 && !path.endsWith("/api/auth") && !reauthed && Net.reauth) {
      reauthed = true;
      try { await Net.reauth(); continue; } catch (_) { /* упадёт ниже */ }
    }
    if (!res.ok) throw normalizeError(res.status, data);
    return data;
  }
}

// ---------- тосты ----------
function toast(msg, kind = "") {
  const root = $("#toast-root");
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  root.appendChild(el);
  setTimeout(() => el.remove(), kind === "err" ? 4200 : 2800);
}

// ---------- шторки и подтверждения ----------
const Sheets = {
  stack: [],
  get top() { return this.stack[this.stack.length - 1]; },
  open(render, { dismissible = true, onClose } = {}) {
    const el = document.createElement("div");
    el.className = "overlay";
    const item = { el, render, onClose, dismissible };
    item.draw = () => {
      const scroller = el.firstElementChild;
      const y = scroller ? scroller.scrollTop : 0;
      el.innerHTML = `<div class="sheet" role="dialog" aria-modal="true"><div class="grab"></div>${str(render())}</div>`;
      if (y) el.firstElementChild.scrollTop = y;
    };
    item.draw();
    el.addEventListener("click", (e) => { if (e.target === el && item.dismissible) Sheets.close(item); });
    $("#sheet-root").appendChild(el);
    document.body.classList.add("lock");
    this.stack.push(item);
    return item;
  },
  close(item = this.top) {
    if (!item) return;
    const i = this.stack.indexOf(item);
    if (i < 0) return;
    this.stack.splice(i, 1);
    item.el.remove();
    if (!this.stack.length) document.body.classList.remove("lock");
    if (item.onClose) item.onClose();
  },
  closeAll() { while (this.stack.length) this.close(); },
};

function askConfirm({ title, text, confirm = "Подтвердить", cancel = "Отмена", danger = false }) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      Sheets.close(item);
      resolve(v);
    };
    const item = Sheets.open(
      () => html`
        <h2>${title}</h2>
        <div class="text">${text}</div>
        <div class="btn-row v">
          <button class="btn ${danger ? "danger solid" : "primary"}" data-act="_yes">${confirm}</button>
          <button class="btn" data-act="_no">${cancel}</button>
        </div>`,
      { onClose: () => finish(false) }
    );
    item.yes = () => finish(true);
    item.no = () => finish(false);
  });
}

function showInfo({ title, text, button = "Понятно" }) {
  return new Promise((resolve) => {
    const item = Sheets.open(
      () => html`
        <h2>${title}</h2>
        <div class="text">${text}</div>
        <button class="btn primary block" data-act="_no">${button}</button>`,
      { onClose: resolve }
    );
    item.no = () => Sheets.close(item);
  });
}

/** Выполняет действие с блокировкой кнопки от повторных нажатий. */
async function busy(el, fn) {
  if (el && el.classList.contains("loading")) return;
  el && el.classList.add("loading");
  try { return await fn(); }
  finally { el && el.classList && el.classList.remove("loading"); }
}
