from __future__ import annotations

import asyncio
import logging
from html import escape
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.models import Platform

logger = logging.getLogger(__name__)


class MaxBotClient:
    """Тонкий HTTP-клиент MAX Bot API (platform-api2.max.ru)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = settings.max_api_base.rstrip("/")
        self.token = settings.max_bot_token
        self._marker: int | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.token, "Content-Type": "application/json"}

    async def send_message(
        self,
        *,
        user_id: int | None = None,
        chat_id: int | None = None,
        text: str,
        attachments: list[dict] | None = None,
        format: str = "html",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if user_id is not None:
            params["user_id"] = user_id
        if chat_id is not None:
            params["chat_id"] = chat_id
        body: dict[str, Any] = {"text": text, "format": format}
        if attachments:
            body["attachments"] = attachments
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self.base}/messages", params=params, headers=self._headers(), json=body
            )
            r.raise_for_status()
            return r.json()

    async def upload_image(self, path: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base}/uploads",
                params={"type": "image"},
                headers={"Authorization": self.token},
            )
            r.raise_for_status()
            data = r.json()
            upload_url = data.get("url") or data.get("upload_url")
            if not upload_url:
                raise RuntimeError(f"Unexpected upload response: {data}")
            with open(path, "rb") as f:
                up = await client.post(upload_url, files={"data": (Path(path).name, f, "image/png")})
                up.raise_for_status()
                result = up.json() if up.content else {}

        token = result.get("token")
        if not token and isinstance(result.get("photos"), dict):
            first = next(iter(result["photos"].values()), None)
            token = first.get("token") if isinstance(first, dict) else None
        if not token:
            raise RuntimeError(f"Upload returned no token: {result}")
        return {"type": "image", "payload": {"token": token}}

    def app_keyboard(self, staff: bool) -> list[dict]:
        url = self.settings.max_webapp_url
        if staff:
            rows = [[{"type": "open_app", "text": "Открыть кабинет", "web_app": f"{url}#/staff"}]]
        else:
            rows = [
                [{"type": "open_app", "text": "Записаться", "web_app": url}],
                [{"type": "open_app", "text": "Мои записи", "web_app": f"{url}#/mine"}],
            ]
        return [{"type": "inline_keyboard", "payload": {"buttons": rows}}]

    def _contacts(self) -> str:
        lines = []
        if self.settings.shop_address:
            lines.append(f"📍 {escape(self.settings.shop_address)}")
        if self.settings.shop_phone:
            lines.append(f"📞 {escape(self.settings.shop_phone)}")
        return ("\n\n" + "\n".join(lines)) if lines else ""

    async def handle_update(self, update: dict[str, Any]) -> None:
        update_type = update.get("update_type") or update.get("type")
        if update_type not in ("message_created", "bot_started"):
            return

        message = update.get("message") or {}
        sender = message.get("sender") or update.get("user") or {}
        user_id = sender.get("user_id") or sender.get("id")
        if user_id is None:
            return
        user_id = int(user_id)
        if sender.get("is_bot"):
            return

        staff = user_id in self.settings.staff_max or user_id in self.settings.admin_max
        name = escape(self.settings.shop_name)
        if staff:
            text = f"<b>{name}</b> — кабинет мастера.\nЗаписи на день, счета по QR, перенос и график."
        else:
            text = (
                f"<b>{name}</b>\n"
                "Онлайн-запись к мастеру: выберите услугу, мастера и удобное время."
                f"{self._contacts()}"
            )
        await self.send_message(user_id=user_id, text=text, attachments=self.app_keyboard(staff))

    async def poll_forever(self) -> None:
        logger.info("MAX bot long-polling started")
        delay = 1.0
        async with httpx.AsyncClient(timeout=60) as client:
            while True:
                try:
                    params: dict[str, Any] = {"timeout": 30, "types": "message_created,bot_started"}
                    if self._marker is not None:
                        params["marker"] = self._marker
                    r = await client.get(f"{self.base}/updates", params=params, headers=self._headers())
                    if r.status_code >= 400:
                        logger.error("MAX updates error %s: %s", r.status_code, r.text[:200])
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 60)
                        continue
                    delay = 1.0
                    data = r.json()
                    self._marker = data.get("marker", self._marker)
                    for upd in data.get("updates") or []:
                        try:
                            await self.handle_update(upd)
                        except Exception:  # noqa: BLE001
                            logger.exception("MAX handle_update failed")
                except asyncio.CancelledError:
                    raise
                except httpx.TimeoutException:
                    continue
                except Exception:  # noqa: BLE001
                    logger.exception("MAX poll loop error")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)


class MaxNotifier:
    def __init__(self, client: MaxBotClient | None) -> None:
        self.client = client

    async def send_text(self, platform: str, platform_user_id: int, text: str) -> bool:
        if platform != Platform.max.value or not self.client:
            return False
        try:
            await self.client.send_message(user_id=platform_user_id, text=text)
            return True
        except Exception:  # noqa: BLE001
            logger.warning("MAX send_text failed to %s", platform_user_id, exc_info=True)
            return False

    async def send_photo(
        self, platform: str, platform_user_id: int, photo_path: str, caption: str
    ) -> bool:
        if platform != Platform.max.value or not self.client:
            return False
        try:
            attachment = await self.client.upload_image(photo_path)
            await self.client.send_message(
                user_id=platform_user_id, text=caption, attachments=[attachment]
            )
            return True
        except Exception:  # noqa: BLE001
            logger.warning("MAX send_photo failed to %s", platform_user_id, exc_info=True)
            return False
