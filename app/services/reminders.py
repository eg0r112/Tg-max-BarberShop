from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import or_, select

from app.config import Settings
from app.db import SessionLocal
from app.models import Booking
from app.services import booking as svc
from app.services import notify

logger = logging.getLogger(__name__)


async def send_due_reminders(settings: Settings) -> int:
    """Напоминает клиентам за сутки и за 2 часа. Каждое напоминание — одна попытка."""
    sent = 0
    now = svc.now_local(settings)
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                svc._booking_query().where(
                    Booking.status.in_(svc.ACTIVE_STATUSES),
                    or_(Booking.reminded_day.is_(False), Booking.reminded_2h.is_(False)),
                )
            )
        ).scalars().all()

        for b in rows:
            lead = svc.aware(b.starts_at, settings) - now
            if lead <= timedelta(0):
                b.reminded_day = b.reminded_2h = True
                continue

            kind: str | None = None
            if not b.reminded_2h and lead <= timedelta(hours=2):
                kind = "2h"
                b.reminded_2h = b.reminded_day = True
            elif not b.reminded_day and lead <= timedelta(hours=24):
                kind = "day"
                b.reminded_day = True
            if kind is None:
                continue

            data = svc.booking_to_out(b, settings)
            ok = await notify.notify_text(
                b.client.platform.value,
                b.client.platform_user_id,
                notify.reminder_text(settings, data, "day" if kind == "day" else "2h"),
            )
            sent += int(ok)
        await session.commit()
    return sent


async def reminders_loop(settings: Settings) -> None:
    logger.info("Reminders loop started")
    while True:
        try:
            await send_due_reminders(settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Reminders iteration failed")
        await asyncio.sleep(60)
