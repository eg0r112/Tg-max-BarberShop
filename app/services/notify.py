from __future__ import annotations

"""Уведомления в Telegram и MAX. Любой сбой доставки логируется и не ломает основной сценарий."""

import asyncio
import logging
from datetime import datetime
from html import escape
from typing import Any, Protocol

from app.config import Settings

logger = logging.getLogger(__name__)

MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


class Notifier(Protocol):
    async def send_text(self, platform: str, platform_user_id: int, text: str) -> bool: ...

    async def send_photo(
        self, platform: str, platform_user_id: int, photo_path: str, caption: str
    ) -> bool: ...


_notifier: Notifier | None = None


def set_notifier(notifier: Notifier) -> None:
    global _notifier
    _notifier = notifier


async def notify_text(platform: str, platform_user_id: int, text: str) -> bool:
    if not _notifier:
        return False
    try:
        return bool(await _notifier.send_text(platform, platform_user_id, text))
    except Exception:  # noqa: BLE001
        logger.exception("notify_text failed")
        return False


async def notify_photo(platform: str, platform_user_id: int, photo_path: str, caption: str) -> bool:
    if not _notifier:
        return False
    try:
        return bool(await _notifier.send_photo(platform, platform_user_id, photo_path, caption))
    except Exception:  # noqa: BLE001
        logger.exception("notify_photo failed")
        return False


async def notify_staff(settings: Settings, text: str) -> None:
    """Всем сотрудникам — и в Telegram, и в MAX."""
    tasks = [notify_text("telegram", uid, text) for uid in settings.staff_tg]
    tasks += [notify_text("max", uid, text) for uid in settings.staff_max]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------- тексты

def human_when(dt: datetime) -> str:
    return f"{WEEKDAYS[dt.weekday()]}, {dt.day} {MONTHS[dt.month - 1]} в {dt:%H:%M}"


def _money(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₽"


def _contacts(settings: Settings) -> str:
    lines = []
    if settings.shop_address:
        lines.append(f"📍 {escape(settings.shop_address)}")
    if settings.shop_phone:
        lines.append(f"📞 {escape(settings.shop_phone)}")
    return ("\n" + "\n".join(lines)) if lines else ""


def booking_confirmed_client(settings: Settings, d: dict[str, Any]) -> str:
    return (
        f"✅ <b>Вы записаны — {escape(settings.shop_name)}</b>\n\n"
        f"{escape(d['service_name'])}\n"
        f"🗓 {human_when(d['starts_at'])}\n"
        f"👤 Мастер: {escape(d['master_name'])}\n"
        f"💰 {_money(d['price_rub'])}\n\n"
        f"Напомним за день и за 2 часа до визита."
        f"{_contacts(settings)}"
    )


def booking_new_staff(d: dict[str, Any]) -> str:
    return (
        "🆕 <b>Новая запись</b>\n\n"
        f"{escape(d['service_name'])}\n"
        f"🗓 {human_when(d['starts_at'])}\n"
        f"👤 Мастер: {escape(d['master_name'])}\n"
        f"Клиент: {escape(d['client_name'])}, {escape(d['client_phone'])}"
    )


def booking_cancelled_staff(d: dict[str, Any]) -> str:
    return (
        "❌ <b>Клиент отменил запись</b>\n\n"
        f"{escape(d['service_name'])}\n"
        f"🗓 {human_when(d['starts_at'])}\n"
        f"👤 Мастер: {escape(d['master_name'])}\n"
        f"Клиент: {escape(d['client_name'])}, {escape(d['client_phone'])}"
    )


def booking_rescheduled_staff(d: dict[str, Any], old: datetime) -> str:
    return (
        "🔁 <b>Клиент перенёс запись</b>\n\n"
        f"{escape(d['service_name'])}\n"
        f"Было: {human_when(old)}\n"
        f"Стало: {human_when(d['starts_at'])}\n"
        f"👤 Мастер: {escape(d['master_name'])}\n"
        f"Клиент: {escape(d['client_name'])}, {escape(d['client_phone'])}"
    )


def booking_rescheduled_client(settings: Settings, d: dict[str, Any], old: datetime) -> str:
    return (
        f"🔁 <b>Запись перенесена — {escape(settings.shop_name)}</b>\n\n"
        f"{escape(d['service_name'])}\n"
        f"Было: {human_when(old)}\n"
        f"Стало: <b>{human_when(d['starts_at'])}</b>\n"
        f"👤 Мастер: {escape(d['master_name'])}"
    )


def booking_removed_client(settings: Settings, d: dict[str, Any]) -> str:
    return (
        f"❌ <b>Запись отменена — {escape(settings.shop_name)}</b>\n\n"
        f"{escape(d['service_name'])}\n"
        f"🗓 {human_when(d['starts_at'])}\n\n"
        "Приносим извинения. Вы можете записаться на другое время."
        f"{_contacts(settings)}"
    )


def reminder_text(settings: Settings, d: dict[str, Any], kind: str) -> str:
    head = "⏰ <b>Завтра у вас визит</b>" if kind == "day" else "⏰ <b>Скоро визит — через пару часов</b>"
    return (
        f"{head}\n\n"
        f"{escape(d['service_name'])}\n"
        f"🗓 {human_when(d['starts_at'])}\n"
        f"👤 Мастер: {escape(d['master_name'])}"
        f"{_contacts(settings)}"
    )


def payment_caption(settings: Settings, amount: int, description: str | None) -> str:
    return (
        f"💳 <b>{escape(settings.shop_name)}</b>\n"
        f"{escape(description or 'Оплата услуг')}\n\n"
        f"К оплате: <b>{_money(amount)}</b>\n\n"
        "Откройте приложение банка и отсканируйте QR-код."
    )


def client_blocked(settings: Settings) -> str:
    return (
        f"⛔ <b>Доступ к записи ограничен — {escape(settings.shop_name)}</b>\n\n"
        "Все ваши записи отменены. Если это ошибка — позвоните в салон."
        f"{_contacts(settings)}"
    )


def client_unblocked(settings: Settings) -> str:
    return (
        f"✅ <b>Доступ восстановлен — {escape(settings.shop_name)}</b>\n\n"
        "Вы снова можете записываться через приложение."
        f"{_contacts(settings)}"
    )
