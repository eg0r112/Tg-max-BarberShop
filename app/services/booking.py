from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models import (
    Booking,
    BookingStatus,
    Master,
    MasterBreak,
    MasterSchedule,
    MasterTimeOff,
    Payment,
    PaymentStatus,
    Service,
    User,
    UserRole,
)

ACTIVE_STATUSES = {BookingStatus.pending, BookingStatus.confirmed}

# Все изменения расписания идут через одну блокировку — защита от двойной записи
# на одно время (приложение запускается одним процессом).
_booking_lock = asyncio.Lock()


class BookingError(ValueError):
    """Ошибка бизнес-логики с машинным кодом для UI."""

    def __init__(self, message: str, code: str = "error", **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


# ---------------------------------------------------------------- время

def tz_of(settings: Settings) -> ZoneInfo:
    return ZoneInfo(settings.shop_timezone)


def now_local(settings: Settings) -> datetime:
    return datetime.now(tz_of(settings))


def today_local(settings: Settings) -> date:
    return now_local(settings).date()


def aware(dt: datetime, settings: Settings) -> datetime:
    """SQLite отдаёт naive datetime (время по часам салона) — приводим к TZ салона."""
    tz = tz_of(settings)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _combine(day: date, t: time, settings: Settings) -> datetime:
    return datetime.combine(day, t, tzinfo=tz_of(settings))


def _overlaps(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
    return a0 < b1 and a1 > b0


def normalize_phone(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if not 11 <= len(digits) <= 15:
        raise BookingError("Проверьте номер телефона", code="bad_phone")
    return "+" + digits


# ---------------------------------------------------------------- контекст мастера

@dataclass
class MasterCtx:
    master_id: int
    weekly: dict[int, tuple[time, time, bool]] = field(default_factory=dict)
    days_off: set[date] = field(default_factory=set)
    breaks: dict[date, list[tuple[time, time]]] = field(default_factory=dict)
    bookings: list[tuple[datetime, datetime]] = field(default_factory=list)


async def load_ctx(
    session: AsyncSession,
    settings: Settings,
    master_id: int,
    first_day: date,
    last_day: date,
    exclude_booking_id: int | None = None,
) -> MasterCtx:
    ctx = MasterCtx(master_id=master_id)

    for row in (
        await session.execute(select(MasterSchedule).where(MasterSchedule.master_id == master_id))
    ).scalars():
        ctx.weekly[row.weekday] = (row.start_time, row.end_time, bool(row.is_day_off))

    for row in (
        await session.execute(
            select(MasterTimeOff).where(
                MasterTimeOff.master_id == master_id,
                MasterTimeOff.day >= first_day,
                MasterTimeOff.day <= last_day,
            )
        )
    ).scalars():
        ctx.days_off.add(row.day)

    for row in (
        await session.execute(
            select(MasterBreak).where(
                MasterBreak.master_id == master_id,
                MasterBreak.day >= first_day,
                MasterBreak.day <= last_day,
            )
        )
    ).scalars():
        ctx.breaks.setdefault(row.day, []).append((row.start_time, row.end_time))

    range_start = _combine(first_day, time.min, settings)
    range_end = _combine(last_day, time.min, settings) + timedelta(days=1)
    q = select(Booking).where(
        Booking.master_id == master_id,
        Booking.status.in_(ACTIVE_STATUSES),
        Booking.starts_at < range_end,
        Booking.ends_at > range_start,
    )
    if exclude_booking_id is not None:
        q = q.where(Booking.id != exclude_booking_id)
    for b in (await session.execute(q)).scalars():
        ctx.bookings.append((aware(b.starts_at, settings), aware(b.ends_at, settings)))
    return ctx


def day_window(ctx: MasterCtx, settings: Settings, day: date) -> tuple[datetime, datetime] | None:
    if day in ctx.days_off:
        return None
    sched = ctx.weekly.get(day.weekday())
    if sched:
        start, end, is_off = sched
        if is_off:
            return None
    else:
        start = time.fromisoformat(settings.workday_start)
        end = time.fromisoformat(settings.workday_end)
    return _combine(day, start, settings), _combine(day, end, settings)


def compute_slots(
    ctx: MasterCtx,
    settings: Settings,
    day: date,
    duration_min: int,
    *,
    now: datetime,
    lead_minutes: int,
) -> list[tuple[datetime, datetime]]:
    window = day_window(ctx, settings, day)
    if not window:
        return []
    day_start, day_end = window
    duration = timedelta(minutes=duration_min)
    step = timedelta(minutes=max(5, settings.slot_minutes))
    earliest = now + timedelta(minutes=lead_minutes)
    breaks = [
        (_combine(day, b0, settings), _combine(day, b1, settings)) for b0, b1 in ctx.breaks.get(day, [])
    ]

    out: list[tuple[datetime, datetime]] = []
    cursor = day_start
    while cursor + duration <= day_end:
        end = cursor + duration
        if cursor >= earliest:
            busy = any(_overlaps(cursor, end, b0, b1) for b0, b1 in ctx.bookings) or any(
                _overlaps(cursor, end, b0, b1) for b0, b1 in breaks
            )
            if not busy:
                out.append((cursor, end))
        cursor += step
    return out


# ---------------------------------------------------------------- каталог

async def active_masters(session: AsyncSession) -> list[Master]:
    rows = await session.execute(
        select(Master).where(Master.is_active.is_(True)).order_by(Master.sort_order, Master.id)
    )
    return list(rows.scalars().all())


async def _service_or_error(session: AsyncSession, service_id: int) -> Service:
    service = await session.get(Service, service_id)
    if not service or not service.is_active:
        raise BookingError("Услуга недоступна", code="service_unavailable")
    return service


async def _master_pool(session: AsyncSession, master_id: int | None) -> list[Master]:
    if master_id is None:
        pool = await active_masters(session)
    else:
        m = await session.get(Master, master_id)
        pool = [m] if m and m.is_active else []
    if not pool:
        raise BookingError("Мастер недоступен", code="master_unavailable")
    return pool


def _lead(settings: Settings, staff: bool) -> int:
    return 0 if staff else settings.min_lead_minutes


async def list_days(
    session: AsyncSession,
    settings: Settings,
    *,
    service_id: int,
    master_id: int | None,
    exclude_booking_id: int | None = None,
    staff: bool = False,
) -> list[dict]:
    """Дни в горизонте записи с количеством свободных окон."""
    service = await _service_or_error(session, service_id)
    pool = await _master_pool(session, master_id)
    now = now_local(settings)
    first = now.date()
    last = first + timedelta(days=settings.booking_horizon_days)

    starts_by_day: dict[date, set[datetime]] = {}
    for m in pool:
        ctx = await load_ctx(session, settings, m.id, first, last, exclude_booking_id)
        d = first
        while d <= last:
            for s, _ in compute_slots(
                ctx, settings, d, service.duration_minutes, now=now, lead_minutes=_lead(settings, staff)
            ):
                starts_by_day.setdefault(d, set()).add(s)
            d += timedelta(days=1)

    out = []
    d = first
    while d <= last:
        out.append({"day": d, "free": len(starts_by_day.get(d, ()))})
        d += timedelta(days=1)
    return out


async def list_slots(
    session: AsyncSession,
    settings: Settings,
    *,
    service_id: int,
    master_id: int | None,
    day: date,
    exclude_booking_id: int | None = None,
    staff: bool = False,
) -> list[dict]:
    service = await _service_or_error(session, service_id)
    pool = await _master_pool(session, master_id)
    now = now_local(settings)
    if day < now.date() or day > now.date() + timedelta(days=settings.booking_horizon_days):
        return []

    merged: dict[datetime, dict] = {}
    for m in pool:
        ctx = await load_ctx(session, settings, m.id, day, day, exclude_booking_id)
        for s, e in compute_slots(
            ctx, settings, day, service.duration_minutes, now=now, lead_minutes=_lead(settings, staff)
        ):
            item = merged.setdefault(
                s, {"starts_at": s, "ends_at": e, "label": s.strftime("%H:%M"), "master_ids": []}
            )
            item["master_ids"].append(m.id)
    return [merged[k] for k in sorted(merged)]


async def next_available(
    session: AsyncSession,
    settings: Settings,
    *,
    service_id: int,
) -> dict:
    """Ближайшее окно у каждого мастера и у «любого»."""
    service = await _service_or_error(session, service_id)
    masters = await active_masters(session)
    now = now_local(settings)
    first = now.date()
    last = first + timedelta(days=settings.booking_horizon_days)

    per_master: list[dict] = []
    earliest: datetime | None = None
    for m in masters:
        ctx = await load_ctx(session, settings, m.id, first, last)
        found: datetime | None = None
        d = first
        while d <= last and not found:
            slots = compute_slots(
                ctx, settings, d, service.duration_minutes, now=now, lead_minutes=settings.min_lead_minutes
            )
            if slots:
                found = slots[0][0]
            d += timedelta(days=1)
        per_master.append({"master_id": m.id, "starts_at": found})
        if found and (earliest is None or found < earliest):
            earliest = found
    return {"masters": per_master, "any": earliest}


# ---------------------------------------------------------------- загрузка записей

def _booking_query():
    return select(Booking).options(
        selectinload(Booking.service),
        selectinload(Booking.master),
        selectinload(Booking.client),
        selectinload(Booking.payments),
    )


async def get_booking(
    session: AsyncSession, booking_id: int, *, fresh: bool = False
) -> Booking | None:
    q = _booking_query().where(Booking.id == booking_id)
    if fresh:
        q = q.execution_options(populate_existing=True)
    return (await session.execute(q)).scalar_one_or_none()


def payment_summary(b: Booking) -> dict | None:
    live = [p for p in b.payments if p.status != PaymentStatus.cancelled]
    if not live:
        return None
    paid = [p for p in live if p.status == PaymentStatus.paid]
    p = max(paid or live, key=lambda x: x.id)
    return {"id": p.id, "status": p.status.value, "amount_rub": p.amount_rub}


def can_client_modify(settings: Settings, b: Booking, now: datetime) -> bool:
    if b.status not in ACTIVE_STATUSES:
        return False
    return aware(b.starts_at, settings) - now >= timedelta(hours=settings.cancel_min_hours)


def booking_to_out(b: Booking, settings: Settings) -> dict:
    now = now_local(settings)
    starts = aware(b.starts_at, settings)
    ends = aware(b.ends_at, settings)
    return {
        "id": b.id,
        "service_id": b.service_id,
        "service_name": b.service.name,
        "master_id": b.master_id,
        "master_name": b.master.name,
        "starts_at": starts,
        "ends_at": ends,
        "status": b.status.value,
        "client_name": b.client_name,
        "client_phone": b.client_phone,
        "client_user_id": b.client_id,
        "price_rub": b.service.price_rub,
        "duration_minutes": b.service.duration_minutes,
        "comment": b.comment,
        "is_upcoming": b.status in ACTIVE_STATUSES and ends > now,
        "can_modify": can_client_modify(settings, b, now),
        "payment": payment_summary(b),
    }


async def my_bookings(session: AsyncSession, settings: Settings, user_id: int) -> list[Booking]:
    rows = (
        await session.execute(
            _booking_query().where(Booking.client_id == user_id).order_by(Booking.starts_at.desc()).limit(80)
        )
    ).scalars().all()
    return list(rows)


async def bookings_for_day(
    session: AsyncSession,
    settings: Settings,
    *,
    day: date,
    master_id: int | None,
) -> list[Booking]:
    start = _combine(day, time.min, settings)
    end = start + timedelta(days=1)
    q = (
        _booking_query()
        .where(
            Booking.starts_at >= start,
            Booking.starts_at < end,
            Booking.status != BookingStatus.cancelled,
        )
        .order_by(Booking.starts_at)
    )
    if master_id:
        q = q.where(Booking.master_id == master_id)
    return list((await session.execute(q)).scalars().all())


async def bookings_overlapping(
    session: AsyncSession,
    settings: Settings,
    master_id: int,
    start: datetime,
    end: datetime,
) -> list[Booking]:
    rows = (
        await session.execute(
            _booking_query().where(
                Booking.master_id == master_id,
                Booking.status.in_(ACTIVE_STATUSES),
                Booking.starts_at < end,
                Booking.ends_at > start,
            )
        )
    ).scalars().all()
    return list(rows)


# ---------------------------------------------------------------- создание / перенос / отмена

def _reminder_flags(settings: Settings, starts_at: datetime) -> tuple[bool, bool]:
    """Если до визита уже меньше суток / двух часов — соответствующие напоминания не шлём."""
    lead = aware(starts_at, settings) - now_local(settings)
    return lead < timedelta(hours=24, minutes=30), lead < timedelta(hours=2, minutes=30)


async def _client_conflicts(
    session: AsyncSession,
    settings: Settings,
    *,
    client_id: int,
    starts_at: datetime,
    ends_at: datetime,
    exclude_booking_id: int | None,
    check_limit: bool,
) -> None:
    rows = (
        await session.execute(
            select(Booking).where(Booking.client_id == client_id, Booking.status.in_(ACTIVE_STATUSES))
        )
    ).scalars().all()
    now = now_local(settings)
    future = 0
    for b in rows:
        if b.id == exclude_booking_id:
            continue
        b0, b1 = aware(b.starts_at, settings), aware(b.ends_at, settings)
        if _overlaps(starts_at, ends_at, b0, b1):
            raise BookingError("У вас уже есть запись на это время", code="client_overlap")
        if b1 > now:
            future += 1
    if check_limit and future >= settings.max_active_bookings:
        raise BookingError(
            f"Можно иметь не более {settings.max_active_bookings} активных записей. "
            "Отмените одну из них, чтобы записаться снова.",
            code="too_many",
        )


async def create_booking(
    session: AsyncSession,
    settings: Settings,
    *,
    client: User,
    service_id: int,
    master_id: int | None,
    starts_at: datetime,
    client_name: str,
    client_phone: str,
    comment: str | None,
) -> Booking:
    if client.is_blocked:
        raise BookingError("Доступ ограничен. Обратитесь в салон.", code="blocked")
    name = " ".join(client_name.split())
    if len(name) < 2:
        raise BookingError("Укажите имя", code="bad_name")
    phone = normalize_phone(client_phone)
    comment = (comment or "").strip() or None

    async with _booking_lock:
        service = await _service_or_error(session, service_id)
        pool = await _master_pool(session, master_id)
        starts = aware(starts_at, settings)
        ends = starts + timedelta(minutes=service.duration_minutes)
        now = now_local(settings)
        if starts.date() > now.date() + timedelta(days=settings.booking_horizon_days):
            raise BookingError("Запись доступна не далее чем на "
                               f"{settings.booking_horizon_days} дней вперёд", code="too_far")

        eligible: list[tuple[Master, int]] = []
        for m in pool:
            ctx = await load_ctx(session, settings, m.id, starts.date(), starts.date())
            slots = compute_slots(
                ctx, settings, starts.date(), service.duration_minutes,
                now=now, lead_minutes=settings.min_lead_minutes,
            )
            if any(s == starts for s, _ in slots):
                load = sum(1 for b0, _ in ctx.bookings if b0.date() == starts.date())
                eligible.append((m, load))
        if not eligible:
            raise BookingError("Это время уже занято. Выберите другое.", code="slot_taken")

        eligible.sort(key=lambda x: (x[1], x[0].sort_order, x[0].id))
        master = eligible[0][0]

        await _client_conflicts(
            session, settings, client_id=client.id, starts_at=starts, ends_at=ends,
            exclude_booking_id=None, check_limit=True,
        )

        r_day, r_2h = _reminder_flags(settings, starts)
        booking = Booking(
            client_id=client.id,
            master_id=master.id,
            service_id=service.id,
            starts_at=starts,
            ends_at=ends,
            status=BookingStatus.confirmed,
            client_name=name,
            client_phone=phone,
            comment=comment,
            reminded_day=r_day,
            reminded_2h=r_2h,
        )
        session.add(booking)
        # Имя в профиле не перезаписываем при записи — его клиент задаёт отдельно
        client.phone = phone
        await session.commit()
        return await get_booking(session, booking.id)  # type: ignore[return-value]


async def reschedule_booking(
    session: AsyncSession,
    settings: Settings,
    *,
    booking_id: int,
    starts_at: datetime,
    master_id: int | None,
    staff: bool,
    actor_id: int,
) -> tuple[Booking, datetime]:
    """Возвращает (запись, прежнее время)."""
    async with _booking_lock:
        booking = await get_booking(session, booking_id)
        if not booking or booking.status not in ACTIVE_STATUSES:
            raise BookingError("Запись не найдена или уже неактивна", code="not_found")
        now = now_local(settings)
        if not staff:
            if booking.client_id != actor_id:
                raise BookingError("Нет доступа", code="forbidden")
            if not can_client_modify(settings, booking, now):
                raise BookingError(
                    f"Перенести запись можно не позднее чем за {settings.cancel_min_hours} ч. "
                    "Позвоните в салон.",
                    code="too_late",
                )

        target_master_id = master_id or booking.master_id
        master = await session.get(Master, target_master_id)
        if not master or not master.is_active:
            raise BookingError("Мастер недоступен", code="master_unavailable")

        starts = aware(starts_at, settings)
        ends = starts + timedelta(minutes=booking.service.duration_minutes)
        if starts.date() > now.date() + timedelta(days=settings.booking_horizon_days):
            raise BookingError("Слишком далеко вперёд", code="too_far")

        ctx = await load_ctx(
            session, settings, target_master_id, starts.date(), starts.date(), exclude_booking_id=booking.id
        )
        slots = compute_slots(
            ctx, settings, starts.date(), booking.service.duration_minutes,
            now=now, lead_minutes=_lead(settings, staff),
        )
        if not any(s == starts for s, _ in slots):
            raise BookingError("Это время уже занято. Выберите другое.", code="slot_taken")

        await _client_conflicts(
            session, settings, client_id=booking.client_id, starts_at=starts, ends_at=ends,
            exclude_booking_id=booking.id, check_limit=False,
        )

        old_start = aware(booking.starts_at, settings)
        booking.master_id = target_master_id
        booking.starts_at = starts
        booking.ends_at = ends
        booking.status = BookingStatus.confirmed
        booking.reminded_day, booking.reminded_2h = _reminder_flags(settings, starts)
        await session.commit()
        return (await get_booking(session, booking.id, fresh=True)), old_start  # type: ignore[return-value]


async def cancel_booking(
    session: AsyncSession,
    settings: Settings,
    *,
    booking_id: int,
    actor: User,
    staff: bool,
) -> Booking:
    async with _booking_lock:
        booking = await get_booking(session, booking_id)
        if not booking:
            raise BookingError("Запись не найдена", code="not_found")
        if not staff:
            if booking.client_id != actor.id:
                raise BookingError("Нет доступа", code="forbidden")
            if booking.status not in ACTIVE_STATUSES:
                raise BookingError("Запись уже неактивна", code="inactive")
            if not can_client_modify(settings, booking, now_local(settings)):
                raise BookingError(
                    f"Отменить запись можно не позднее чем за {settings.cancel_min_hours} ч. "
                    "Позвоните в салон.",
                    code="too_late",
                )
        booking.status = BookingStatus.cancelled
        await session.commit()
        return booking


async def delete_booking(session: AsyncSession, booking_id: int) -> None:
    async with _booking_lock:
        booking = await session.get(Booking, booking_id)
        if not booking:
            raise BookingError("Запись не найдена", code="not_found")
        await session.delete(booking)
        await session.commit()


async def set_client_blocked(
    session: AsyncSession,
    *,
    client_id: int,
    blocked: bool,
    actor: User,
) -> dict:
    """Блокирует или разблокирует клиента. При блокировке удаляет все его записи."""
    if actor.id == client_id:
        raise BookingError("Нельзя заблокировать самого себя", code="forbidden")
    async with _booking_lock:
        client = await session.get(User, client_id)
        if not client:
            raise BookingError("Клиент не найден", code="not_found")
        if client.role in (UserRole.staff, UserRole.admin):
            raise BookingError("Нельзя заблокировать сотрудника", code="forbidden")

        deleted = 0
        if blocked:
            rows = (
                await session.execute(
                    select(Booking)
                    .options(selectinload(Booking.payments))
                    .where(Booking.client_id == client_id)
                )
            ).scalars().all()
            for b in rows:
                for p in b.payments:
                    if p.status in (PaymentStatus.pending, PaymentStatus.sent):
                        p.status = PaymentStatus.cancelled
                await session.delete(b)
                deleted += 1
            client.is_blocked = True
        else:
            client.is_blocked = False

        await session.commit()
        return {
            "ok": True,
            "client_id": client.id,
            "client_name": client.full_name,
            "is_blocked": bool(client.is_blocked),
            "deleted_bookings": deleted,
            "platform": client.platform.value,
            "platform_user_id": client.platform_user_id,
        }


async def set_status(session: AsyncSession, booking_id: int, status: BookingStatus) -> Booking:
    if status not in (BookingStatus.no_show, BookingStatus.completed, BookingStatus.confirmed):
        raise BookingError("Недопустимый статус", code="bad_status")
    async with _booking_lock:
        booking = await get_booking(session, booking_id)
        if not booking or booking.status == BookingStatus.cancelled:
            raise BookingError("Запись не найдена", code="not_found")
        booking.status = status
        await session.commit()
        return booking


# ---------------------------------------------------------------- график мастера

async def _master_or_error(session: AsyncSession, master_id: int) -> Master:
    master = await session.get(Master, master_id)
    if not master:
        raise BookingError("Мастер не найден", code="not_found")
    return master


async def set_day_off(
    session: AsyncSession,
    settings: Settings,
    *,
    master_id: int,
    day: date,
    reason: str | None,
    force: bool,
) -> None:
    await _master_or_error(session, master_id)
    async with _booking_lock:
        start = _combine(day, time.min, settings)
        conflicts = await bookings_overlapping(session, settings, master_id, start, start + timedelta(days=1))
        if conflicts and not force:
            raise BookingError(
                f"На этот день уже есть записей: {len(conflicts)}",
                code="has_bookings",
                count=len(conflicts),
            )
        existing = (
            await session.execute(
                select(MasterTimeOff).where(MasterTimeOff.master_id == master_id, MasterTimeOff.day == day)
            )
        ).scalar_one_or_none()
        if existing:
            existing.reason = reason
        else:
            session.add(MasterTimeOff(master_id=master_id, day=day, reason=reason))
        await session.commit()


async def clear_day_off(session: AsyncSession, *, master_id: int, day: date) -> None:
    rows = (
        await session.execute(
            select(MasterTimeOff).where(MasterTimeOff.master_id == master_id, MasterTimeOff.day == day)
        )
    ).scalars().all()
    for row in rows:
        await session.delete(row)
    await session.commit()


async def add_break(
    session: AsyncSession,
    settings: Settings,
    *,
    master_id: int,
    day: date,
    start_time: time,
    end_time: time,
    reason: str | None,
    force: bool,
) -> MasterBreak:
    if end_time <= start_time:
        raise BookingError("Конец перерыва должен быть позже начала", code="bad_range")
    await _master_or_error(session, master_id)
    async with _booking_lock:
        conflicts = await bookings_overlapping(
            session, settings, master_id, _combine(day, start_time, settings), _combine(day, end_time, settings)
        )
        if conflicts and not force:
            raise BookingError(
                f"На это время уже есть записей: {len(conflicts)}",
                code="has_bookings",
                count=len(conflicts),
            )
        row = MasterBreak(
            master_id=master_id, day=day, start_time=start_time, end_time=end_time, reason=reason
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def delete_break(session: AsyncSession, break_id: int) -> None:
    row = await session.get(MasterBreak, break_id)
    if not row:
        raise BookingError("Перерыв не найден", code="not_found")
    await session.delete(row)
    await session.commit()


async def list_availability(
    session: AsyncSession, settings: Settings, *, master_id: int, day: date
) -> dict:
    await _master_or_error(session, master_id)
    ctx_off = (
        await session.execute(
            select(MasterTimeOff).where(MasterTimeOff.master_id == master_id, MasterTimeOff.day == day)
        )
    ).scalar_one_or_none()
    breaks = (
        await session.execute(
            select(MasterBreak)
            .where(MasterBreak.master_id == master_id, MasterBreak.day == day)
            .order_by(MasterBreak.start_time)
        )
    ).scalars().all()
    sched = (
        await session.execute(
            select(MasterSchedule).where(
                MasterSchedule.master_id == master_id, MasterSchedule.weekday == day.weekday()
            )
        )
    ).scalar_one_or_none()

    if sched:
        weekly_off = bool(sched.is_day_off)
        work_start, work_end = sched.start_time, sched.end_time
    else:
        weekly_off = False
        work_start = time.fromisoformat(settings.workday_start)
        work_end = time.fromisoformat(settings.workday_end)

    return {
        "master_id": master_id,
        "day": day,
        "is_day_off": ctx_off is not None or weekly_off,
        "day_off_set": ctx_off is not None,
        "weekly_off": weekly_off,
        "reason": ctx_off.reason if ctx_off else None,
        "work_start": None if weekly_off else work_start.strftime("%H:%M"),
        "work_end": None if weekly_off else work_end.strftime("%H:%M"),
        "breaks": [
            {
                "id": b.id,
                "start": b.start_time.strftime("%H:%M"),
                "end": b.end_time.strftime("%H:%M"),
                "reason": b.reason,
            }
            for b in breaks
        ],
    }


async def get_week(session: AsyncSession, settings: Settings, master_id: int) -> list[dict]:
    await _master_or_error(session, master_id)
    rows = {
        r.weekday: r
        for r in (
            await session.execute(select(MasterSchedule).where(MasterSchedule.master_id == master_id))
        ).scalars()
    }
    default_start = time.fromisoformat(settings.workday_start)
    default_end = time.fromisoformat(settings.workday_end)
    out = []
    for wd in range(7):
        r = rows.get(wd)
        out.append(
            {
                "weekday": wd,
                "is_day_off": bool(r.is_day_off) if r else False,
                "start": (r.start_time if r else default_start).strftime("%H:%M"),
                "end": (r.end_time if r else default_end).strftime("%H:%M"),
            }
        )
    return out


async def set_week(
    session: AsyncSession, *, master_id: int, items: list[dict]
) -> None:
    await _master_or_error(session, master_id)
    if len(items) != 7 or {i["weekday"] for i in items} != set(range(7)):
        raise BookingError("Нужно передать все 7 дней недели", code="bad_schedule")
    parsed: list[tuple[int, time, time, bool]] = []
    for i in items:
        start = time.fromisoformat(i["start"])
        end = time.fromisoformat(i["end"])
        if not i["is_day_off"] and end <= start:
            raise BookingError("Конец рабочего дня должен быть позже начала", code="bad_range")
        parsed.append((i["weekday"], start, end, bool(i["is_day_off"])))

    async with _booking_lock:
        existing = {
            r.weekday: r
            for r in (
                await session.execute(select(MasterSchedule).where(MasterSchedule.master_id == master_id))
            ).scalars()
        }
        for wd, start, end, off in parsed:
            row = existing.get(wd)
            if row:
                row.start_time, row.end_time, row.is_day_off = start, end, off
            else:
                session.add(
                    MasterSchedule(
                        master_id=master_id, weekday=wd, start_time=start, end_time=end, is_day_off=off
                    )
                )
        await session.commit()
