"""Сквозная проверка API на запущенном сервере (нужен DEV_MODE=true).

    python scripts/smoke_test.py [http://127.0.0.1:8000]

Созданные тестовые записи удаляются в конце.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
failed: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    print(("OK   " if cond else "FAIL ") + name, extra if not cond else "")
    if not cond:
        failed.append(name)


def login(uid: int, name: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=20)
    r = c.post("/api/auth", json={"platform": "telegram", "user_id": uid, "full_name": name})
    r.raise_for_status()
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    c.role = r.json()["role"]  # type: ignore[attr-defined]
    return c


client = login(7001, "Тест Клиент")
client2 = login(7002, "Другой Клиент")
staff = login(9001, "Мастер")
check("роль клиента", client.role == "client")
check("роль мастера", staff.role in ("staff", "admin"))
check("клиент не видит кабинет", client.get("/api/staff/day", params={"day": str(date.today())}).status_code == 403)
check("без токена 401", httpx.get(BASE + "/api/services").status_code == 401)

services = client.get("/api/services").json()
masters = client.get("/api/masters").json()
check("услуги с категориями", len(services) >= 20 and all(s["category"] for s in services))
check("3 мастера", len(masters) >= 3)
svc = next(s for s in services if s["duration_minutes"] == 45)
m1, m2 = masters[0]["id"], masters[1]["id"]

days = client.get("/api/availability/days", params={"service_id": svc["id"]}).json()
free_days = [d for d in days if d["free"] > 0]
check("есть свободные дни", len(free_days) > 5)
day = free_days[2]["day"]
slots = client.get("/api/slots", params={"service_id": svc["id"], "day": day}).json()
check("есть слоты", len(slots) > 4)

created: list[int] = []


def book(c: httpx.Client, slot: dict, master_id: int | None) -> httpx.Response:
    return c.post(
        "/api/bookings",
        json={
            "service_id": svc["id"], "master_id": master_id, "starts_at": slot["starts_at"],
            "client_name": "Тест", "client_phone": "+7 (900) 111-22-33",
        },
    )


r = book(client, slots[0], m1)
check("запись создана", r.status_code == 200, r.text)
b1 = r.json()
created.append(b1["id"])
check("телефон нормализован", b1["client_phone"].startswith("+7"), b1["client_phone"])

# вторая запись в тот же день, позже
later = next(s for s in slots if s["starts_at"] > b1["ends_at"] and m1 in s["master_ids"])
r = book(client, later, m1)
check("вторая запись в тот же день", r.status_code == 200, r.text)
if r.status_code == 200:
    created.append(r.json()["id"])

r = book(client2, slots[0], m1)
check("двойная запись к одному мастеру -> 409", r.status_code == 409, r.text)
check("код ошибки slot_taken", r.json().get("detail", {}).get("code") == "slot_taken", r.text)

r = book(client, slots[0], m2)
check("клиент не может быть в двух местах сразу", r.status_code == 409, r.text)

r = book(client2, slots[0], None)
check("любой мастер — назначен другой", r.status_code == 200 and r.json()["master_id"] != m1, r.text)
if r.status_code == 200:
    created.append(r.json()["id"])

mine = client.get("/api/bookings/mine").json()
check("мои записи", len([b for b in mine if b["id"] in created]) >= 2)

# перенос клиентом
new_slot = next(s for s in slots if s["starts_at"] > b1["ends_at"] and s is not later and m1 in s["master_ids"] and s["starts_at"] != later["starts_at"] and s["starts_at"] > later["ends_at"])
r = client.post(f"/api/bookings/{b1['id']}/reschedule", json={"starts_at": new_slot["starts_at"]})
check("перенос клиентом", r.status_code == 200, r.text)

# чужую запись трогать нельзя
r = client2.post(f"/api/bookings/{b1['id']}/cancel")
check("чужую запись отменить нельзя", r.status_code in (403, 404), r.text)

# выходной и перерыв с конфликтом
r = staff.post("/api/staff/day-off", json={"master_id": m1, "day": day})
check("выходной при наличии записей -> has_bookings", r.status_code == 409 and r.json()["detail"]["code"] == "has_bookings", r.text)
r = staff.post("/api/staff/breaks", json={"master_id": m2, "day": day, "start_time": "13:00", "end_time": "14:00"})
check("перерыв добавлен", r.status_code == 200, r.text)
break_id = r.json().get("id")
av = staff.get("/api/staff/availability", params={"master_id": m2, "day": day}).json()
check("перерыв виден", any(b["id"] == break_id for b in av["breaks"]), av)
slots2 = client.get("/api/slots", params={"service_id": svc["id"], "day": day, "master_id": m2}).json()
check("слоты не пересекаются с перерывом", all(not ("13:00" <= s["label"] < "14:00") for s in slots2))
staff.delete(f"/api/staff/breaks/{break_id}")

# оплата
if client.get("/api/shop").json()["payments_ready"]:
    r = staff.post("/api/staff/payments", json={"booking_id": b1["id"], "amount_rub": 1800})
    check("счёт создан", r.status_code == 200, r.text)
    if r.status_code == 200:
        p = r.json()
        check("QR доступен", httpx.get(BASE + p["qr_url"]).status_code == 200)
        r2 = staff.post("/api/staff/payments", json={"booking_id": b1["id"], "amount_rub": 1900})
        check("повторный счёт -> 409 payment_exists", r2.status_code == 409 and r2.json()["detail"]["code"] == "payment_exists", r2.text)
        r3 = staff.post("/api/staff/payments", json={"booking_id": b1["id"], "amount_rub": 1900, "replace": True})
        check("замена счёта", r3.status_code == 200, r3.text)
        r4 = staff.post(f"/api/staff/payments/{r3.json()['id']}/paid")
        check("оплата отмечена", r4.status_code == 200 and r4.json()["status"] == "paid", r4.text)
        r5 = staff.post("/api/staff/payments", json={"booking_id": b1["id"], "amount_rub": 100, "replace": True})
        check("оплаченной записи счёт не выставить", r5.status_code == 409, r5.text)
else:
    print("SKIP оплата (реквизиты не заполнены)")

# график
week = staff.get(f"/api/staff/schedule/{m1}").json()
check("график 7 дней", len(week["days"]) == 7)
check("сохранение графика", staff.put(f"/api/staff/schedule/{m1}", json=week).status_code == 200)

# день мастера
dd = staff.get("/api/staff/day", params={"day": day}).json()
check("записи дня в кабинете", len(dd) >= 2)

# клиент отменяет
r = client.post(f"/api/bookings/{created[-2] if len(created) > 2 else created[0]}/cancel")
check("отмена клиентом", r.status_code == 200 and r.json()["status"] == "cancelled", r.text)

# уборка
for bid in created:
    staff.delete(f"/api/staff/bookings/{bid}")

print()
print("ИТОГ:", "все проверки пройдены" if not failed else f"провалено: {failed}")
sys.exit(1 if failed else 0)
