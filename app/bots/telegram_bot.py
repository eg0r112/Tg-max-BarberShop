from __future__ import annotations

"""Telegram-бот на httpx (на этой машине aiohttp/aiogram не достучаться до api.telegram.org)."""

import asyncio
import logging
from html import escape
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from app.config import Settings
from app.db import SessionLocal
from app.models import Platform, User, UserRole
from app.services import auth as auth_svc

logger = logging.getLogger(__name__)


class TelegramBotClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.token = settings.telegram_bot_token
        self.base = f"https://api.telegram.org/bot{self.token}"
        self._offset = 0

    def _webapp_url(self, suffix: str = "") -> str:
        return f"{self.settings.telegram_webapp_url}{suffix}"

    def _is_staff(self, user_id: int) -> bool:
        """Сотрудник по настройкам (.env)."""
        return user_id in self.settings.staff_tg or user_id in self.settings.admin_tg

    async def _effective_staff(self, user_id: int) -> bool:
        """Сотрудник с учётом ручного переключения /role."""
        if not self._is_staff(user_id):
            return False
        async with SessionLocal() as session:
            user = (
                await session.execute(
                    select(User).where(User.platform == Platform.telegram, User.platform_user_id == user_id)
                )
            ).scalar_one_or_none()
        return not (user and user.role_override == UserRole.client)

    async def _toggle_role(self, chat_id: int, user_id: int) -> None:
        """/role — сотрудник переключает себя клиент ⇄ мастер (для проверки клиентского сценария)."""
        if not self._is_staff(user_id):
            await self.send_message(chat_id, "Доступно только сотрудникам.")
            return
        as_client_now = not await self._effective_staff(user_id)
        async with SessionLocal() as session:
            user = await auth_svc.set_role_override(
                session, self.settings, platform=Platform.telegram, platform_user_id=user_id,
                override=None if as_client_now else UserRole.client,
            )
        staff = user.role != UserRole.client
        if staff:
            text = ("Роль: <b>мастер</b> (ваша обычная).\n"
                    "Откройте приложение заново — будет кабинет мастера. Снова стать клиентом: /role")
        else:
            text = ("Роль: <b>клиент</b> — приложение откроется как у обычного клиента.\n"
                    "Откройте его заново. Вернуться к кабинету мастера: /role")
        await self.send_message(chat_id, text, reply_markup=self._kb(staff))

    def _kb(self, staff: bool) -> dict:
        if staff:
            rows = [[{"text": "Открыть кабинет", "web_app": {"url": self._webapp_url("#/staff")}}]]
        else:
            rows = [
                [{"text": "Записаться", "web_app": {"url": self._webapp_url()}}],
                [{"text": "Мои записи", "web_app": {"url": self._webapp_url("#/mine")}}],
            ]
        return {"inline_keyboard": rows}

    def _contacts(self) -> str:
        lines = []
        if self.settings.shop_address:
            lines.append(f"📍 {escape(self.settings.shop_address)}")
        if self.settings.shop_phone:
            lines.append(f"📞 {escape(self.settings.shop_phone)}")
        return ("\n\n" + "\n".join(lines)) if lines else ""

    async def api(self, method: str, **payload: Any) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{self.base}/{method}", json=payload)
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API {method}: {data}")
            return data["result"]

    async def send_message(
        self, chat_id: int, text: str, reply_markup: dict | None = None
    ) -> dict:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            body["reply_markup"] = reply_markup
        return await self.api("sendMessage", **body)

    async def send_photo(self, chat_id: int, photo_path: str, caption: str) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            with open(photo_path, "rb") as f:
                r = await client.post(
                    f"{self.base}/sendPhoto",
                    data={"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"},
                    files={"photo": (Path(photo_path).name, f, "image/png")},
                )
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram sendPhoto: {data}")
            return data["result"]

    async def configure(self) -> None:
        try:
            await self.api(
                "setMyCommands",
                commands=[
                    {"command": "start", "description": "Записаться / открыть приложение"},
                    {"command": "role", "description": "Сотрудникам: переключить клиент ⇄ мастер"},
                ],
            )
            await self.api(
                "setChatMenuButton",
                menu_button={
                    "type": "web_app",
                    "text": "Записаться",
                    "web_app": {"url": self._webapp_url()},
                },
            )
            logger.info("Telegram menu configured → %s", self._webapp_url())
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось настроить меню Telegram-бота")

    async def handle_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = int(user.get("id") or 0)
        if not chat_id or user.get("is_bot"):
            return

        text = (message.get("text") or "").strip()
        if text.startswith("/role"):
            await self._toggle_role(chat_id, user_id)
            return
        if not (text.startswith("/start") or text.startswith("/help") or text.startswith("/staff")):
            return

        name = escape(self.settings.shop_name)
        staff = await self._effective_staff(user_id)

        if text.startswith("/staff") and not staff:
            await self.send_message(chat_id, "Доступно только сотрудникам.")
            return

        if staff and (text.startswith("/start") or text.startswith("/staff")):
            await self.send_message(
                chat_id,
                f"<b>{name}</b> — кабинет мастера.\nЗаписи на день, счета по QR, перенос и график.",
                reply_markup=self._kb(True),
            )
            return

        if text.startswith("/help"):
            await self.send_message(
                chat_id,
                "/start — открыть запись\n"
                + ("/role — переключить роль клиент ⇄ мастер (для проверки)\n" if self._is_staff(user_id) else "")
                + "Записаться, перенести или отменить визит можно в приложении — кнопка внизу."
                f"{self._contacts()}",
            )
            return

        await self.send_message(
            chat_id,
            f"<b>{name}</b>\n"
            "Онлайн-запись к мастеру: выберите услугу, мастера и удобное время."
            f"{self._contacts()}",
            reply_markup=self._kb(False),
        )

    async def poll_forever(self) -> None:
        logger.info("Telegram bot long-polling started (httpx)")
        await self.configure()
        delay = 1.0
        async with httpx.AsyncClient(timeout=60) as client:
            while True:
                try:
                    r = await client.get(
                        f"{self.base}/getUpdates",
                        params={
                            "timeout": 25,
                            "offset": self._offset,
                            "allowed_updates": '["message"]',
                        },
                    )
                    data = r.json()
                    if not data.get("ok"):
                        logger.error("getUpdates error: %s", data)
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 60)
                        continue
                    delay = 1.0
                    for upd in data.get("result") or []:
                        self._offset = max(self._offset, int(upd["update_id"]) + 1)
                        try:
                            await self.handle_update(upd)
                        except Exception:  # noqa: BLE001
                            logger.exception("handle_update failed")
                except asyncio.CancelledError:
                    raise
                except httpx.TimeoutException:
                    continue
                except Exception:  # noqa: BLE001
                    logger.exception("Telegram poll loop error")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)


class TelegramNotifier:
    def __init__(self, client: TelegramBotClient | None) -> None:
        self.client = client

    async def send_text(self, platform: str, platform_user_id: int, text: str) -> bool:
        if platform != Platform.telegram.value or not self.client:
            return False
        try:
            await self.client.send_message(platform_user_id, text)
            return True
        except Exception:  # noqa: BLE001
            logger.warning("TG send_text failed to %s", platform_user_id, exc_info=True)
            return False

    async def send_photo(
        self, platform: str, platform_user_id: int, photo_path: str, caption: str
    ) -> bool:
        if platform != Platform.telegram.value or not self.client:
            return False
        try:
            await self.client.send_photo(platform_user_id, photo_path, caption)
            return True
        except Exception:  # noqa: BLE001
            logger.warning("TG send_photo failed to %s", platform_user_id, exc_info=True)
            return False
