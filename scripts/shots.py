"""Скриншоты мини-приложения для визуальной проверки (нужны playwright и DEV_MODE=true).

    python scripts/shots.py [out_dir]
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "data/shots")
OUT.mkdir(parents=True, exist_ok=True)


def api_client(uid: int, name: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=20)
    r = c.post("/api/auth", json={"platform": "telegram", "user_id": uid, "full_name": name})
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    return c


from datetime import date, timedelta  # noqa: E402


def cleanup() -> None:
    staff = api_client(9001, "Мастер")
    for i in range(0, 14):
        day = (date.today() + timedelta(days=i)).isoformat()
        for b in staff.get("/api/staff/day", params={"day": day}).json():
            if b["client_name"] == "Иван Петров":
                staff.delete(f"/api/staff/bookings/{b['id']}")

cleanup()
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="ru-RU")
    ctx.route("**/telegram.org/**", lambda r: r.abort())
    ctx.route("**/st.max.ru/**", lambda r: r.abort())
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    def shot(name: str, full: bool = False) -> None:
        page.wait_for_timeout(450)
        page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)

    # ---- клиент
    page.goto(f"{BASE}/app/?uid=1001&name=Иван Петров")
    page.wait_for_selector(".hero")
    shot("01_home_empty")
    page.click("[data-act=book-new]")
    page.wait_for_selector(".cat-h")
    shot("02_service")
    page.click("[data-act=b-service] >> nth=2")
    page.wait_for_selector("[data-act=b-master]")
    shot("03_master")
    page.click("[data-act=b-master][data-id=any]")
    page.wait_for_selector(".slot")
    shot("04_time")
    page.click(".slot >> nth=3")
    shot("05_time_selected")
    page.click("[data-act=b-next]")
    page.wait_for_selector("#f-name")
    shot("06_confirm")
    page.fill("#f-name", "")
    page.fill("#f-phone", "123")
    page.click("[data-act=b-submit]")
    shot("07_validation")
    page.fill("#f-name", "Иван Петров")
    page.fill("#f-phone", "89001112233")
    page.click("[data-act=b-submit]")
    page.wait_for_selector(".success")
    shot("08_success")
    page.click("[data-act=done-home]")
    page.wait_for_selector(".bk.next")
    shot("09_home_with_booking")
    page.click("[data-act=tab][data-r=mine]")
    page.wait_for_selector(".bk")
    shot("10_mine")
    page.click("[data-act=cancel]")
    page.wait_for_selector(".overlay")
    shot("11_cancel_confirm")
    page.click("[data-act=_no]")
    page.click("[data-act=resched]")
    page.wait_for_selector(".slot")
    shot("12_resched")

    # ---- мастер
    page.goto(f"{BASE}/app/?staff=1")
    page.wait_for_selector(".hero.mini")
    for _ in range(6):
        page.wait_for_selector(".tl, .empty")
        if page.query_selector(".tl .bk"):
            break
        page.click("[data-act=sday][data-n='1']")
        page.wait_for_timeout(500)
    shot("20_staff_day")
    page.click(".bk [data-act=smenu] >> nth=0")
    page.wait_for_selector(".overlay")
    shot("21_staff_menu")
    page.click("[data-act=_no]")
    page.click("[data-act=pay-new] >> nth=0")
    page.wait_for_selector("#amount")
    shot("22_invoice")
    page.click("[data-act=inv-send]")
    page.wait_for_selector(".overlay >> nth=1")
    shot("23_invoice_confirm")
    page.click("[data-act=_no] >> nth=1")
    page.click("[data-act=_no]")
    page.click("[data-act=tab][data-r=sched]")
    page.wait_for_selector(".card")
    shot("24_schedule")
    page.click("[data-act=break-add]")
    page.wait_for_selector("#br-start")
    shot("25_break_sheet")
    page.click("[data-act=_no]")
    page.click("[data-act=week-edit]")
    page.wait_for_selector(".wk")
    shot("26_week")

    print("console/page errors:", errors or "нет")
    browser.close()

cleanup()
