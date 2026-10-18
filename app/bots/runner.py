from __future__ import annotations

import asyncio
import logging

from app.bots.max_bot import MaxBotClient, MaxNotifier
from app.bots.telegram_bot import TelegramBotClient, TelegramNotifier
from app.config import Settings
from app.services.notify import set_notifier
from app.services.reminders import reminders_loop

logger = logging.getLogger(__name__)


class CompositeNotifier:
    def __init__(self, tg: TelegramNotifier, mx: MaxNotifier) -> None:
        self.tg = tg
        self.mx = mx

    async def send_text(self, platform: str, platform_user_id: int, text: str) -> bool:
        if platform == "telegram":
            return await self.tg.send_text(platform, platform_user_id, text)
        return await self.mx.send_text(platform, platform_user_id, text)

    async def send_photo(
        self, platform: str, platform_user_id: int, photo_path: str, caption: str
    ) -> bool:
        if platform == "telegram":
            return await self.tg.send_photo(platform, platform_user_id, photo_path, caption)
        return await self.mx.send_photo(platform, platform_user_id, photo_path, caption)


class BotRuntime:
    """Запускает ботов и напоминания в фоне и корректно останавливает их."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tasks: list[asyncio.Task] = []
        self.tg_client: TelegramBotClient | None = None
        self.max_client: MaxBotClient | None = None

    async def start(self) -> None:
        s = self.settings
        if s.telegram_bot_token:
            self.tg_client = TelegramBotClient(s)
        if s.max_bot_token:
            self.max_client = MaxBotClient(s)

        set_notifier(
            CompositeNotifier(TelegramNotifier(self.tg_client), MaxNotifier(self.max_client))
        )

        if self.tg_client:
            self.tasks.append(asyncio.create_task(self.tg_client.poll_forever(), name="tg-bot"))
        if self.max_client:
            self.tasks.append(asyncio.create_task(self.max_client.poll_forever(), name="max-bot"))
        if s.reminders_enabled:
            self.tasks.append(asyncio.create_task(reminders_loop(s), name="reminders"))

        if not s.telegram_bot_token and not s.max_bot_token:
            logger.warning("Токены ботов не заданы — уведомления и команды /start отключены")

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
