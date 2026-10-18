from __future__ import annotations

import logging
from datetime import date, time as time_cls
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.models import BookingStatus, Master, Service, User
from app.schemas import (
    AuthRequest,
    AuthResponse,
    BookingCreate,
    BookingOut,
    BookingReschedule,
    BreakCreate,
    DayOffRequest,
    DayOut,
    MasterOut,
    NextAvailableOut,
    PaymentCreate,
    PaymentOut,
    ProfileUpdate,
    ServiceOut,
    ShopInfo,
    SlotOut,
    StatusUpdate,
    WeekSchedule,
)
from app.services import auth as auth_svc
from app.services import booking as svc
from app.services import notify
from app.services import payment_qr as pay_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _out(b, settings: Settings) -> dict:
    return svc.booking_to_out(b, settings)


# ---------------------------------------------------------------- общее

@router.get("/shop", response_model=ShopInfo)
async def shop_info(settings: Settings = Depends(get_settings)) -> ShopInfo:
    return ShopInfo(
        name=settings.shop_name,
        phone=settings.shop_phone,
        address=settings.shop_address,
        timezone=settings.shop_timezone,
        today=svc.today_local(settings),
        workday_start=settings.workday_start,
        workday_end=settings.workday_end,
        horizon_days=settings.booking_horizon_days,
        cancel_min_hours=settings.cancel_min_hours,
        payments_ready=settings.payments_ready,
        dev_mode=settings.dev_mode,
    )


@router.post("/auth", response_model=AuthResponse)
async def auth(
    body: AuthRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    user, token = await auth_svc.authenticate(
        session,
        settings,
        platform=body.platform,
        init_data=body.init_data,
        user_id=body.user_id,
        username=body.username,
        full_name=body.full_name,
    )
    # Заблокированному выдаём токен, чтобы фронт показал понятный экран (без доступа к API)
    return auth_svc.auth_payload(user, token)


async def _catalog(session: AsyncSession) -> tuple[list[dict], list[Master]]:
    rows = await session.execute(
        select(Service).where(Service.is_active.is_(True)).order_by(Service.sort_order, Service.id)
    )
    services = [ServiceOut.model_validate(s).model_dump() for s in rows.scalars().all()]
    masters = await svc.active_masters(session)
    return services, masters


async def _staff_day_full(
    session: AsyncSession,
    settings: Settings,
    *,
    day: date,
    master_id: int | None,
    masters: list[Master] | None = None,
) -> dict:
    """Записи дня + доступность мастеров одним ответом (вместо 1+N запросов)."""
    rows = await svc.bookings_for_day(session, settings, day=day, master_id=master_id)
    if masters is None:
        masters = await svc.active_masters(session)
    ids = [master_id] if master_id else [m.id for m in masters]
    availability = {
        str(mid): await svc.list_availability(session, settings, master_id=mid, day=day) for mid in ids
    }
    return {
        "day": day.isoformat(),
        "master_id": master_id,
        "items": [_out(b, settings) for b in rows],
        "availability": availability,
    }


@router.post("/bootstrap")
async def bootstrap(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Всё для первого экрана одним запросом: салон, вход, каталог и стартовые данные.

    Экономит 3–4 последовательных round-trip при открытии мини-приложения.
    Тело читаем вручную: фронт шлёт его как text/plain, чтобы браузер не делал CORS preflight.
    """
    try:
        body = AuthRequest.model_validate_json((await request.body()) or b"{}")
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "validation", "message": "Проверьте введённые данные"}
        ) from exc
    user, token = await auth_svc.authenticate(
        session,
        settings,
        platform=body.platform,
        init_data=body.init_data,
        user_id=body.user_id,
        username=body.username,
        full_name=body.full_name,
    )
    me = auth_svc.auth_payload(user, token)
    shop = await shop_info(settings)
    out: dict = {
        "shop": shop.model_dump(mode="json"),
        "me": me,
        "services": [],
        "masters": [],
        "mine": None,
        "staff_day": None,
    }
    staff = auth_svc.is_staff(user)
    if me["is_blocked"] and not staff:
        return out

    services, masters = await _catalog(session)
    out["services"] = services
    out["masters"] = [MasterOut.model_validate(m).model_dump() for m in masters]
    if staff:
        out["staff_day"] = await _staff_day_full(
            session, settings, day=svc.today_local(settings), master_id=None, masters=masters
        )
    elif not me["needs_name"]:
        out["mine"] = [_out(b, settings) for b in await svc.my_bookings(session, settings, user.id)]
    return out


@router.patch("/me", response_model=AuthResponse)
async def update_me(
    body: ProfileUpdate,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Смена имени. Доступна даже если needs_name — get_current_user блокирует только is_blocked."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется вход")
    uid = auth_svc.parse_token(settings, authorization.removeprefix("Bearer ").strip())
    user = await session.get(User, uid)
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    user = await auth_svc.update_profile(session, user, full_name=body.full_name)
    return auth_svc.auth_payload(user, auth_svc.issue_token(settings, user))


@router.get("/services", response_model=list[ServiceOut])
async def list_services(
    _: User = Depends(auth_svc.get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Service]:
    rows = await session.execute(
        select(Service).where(Service.is_active.is_(True)).order_by(Service.sort_order, Service.id)
    )
    return list(rows.scalars().all())


@router.get("/masters", response_model=list[MasterOut])
async def list_masters(
    _: User = Depends(auth_svc.get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Master]:
    return await svc.active_masters(session)


# ---------------------------------------------------------------- свободное время

def _exclude_for(user: User, booking_id: int | None, owner_id: int | None) -> int | None:
    """Параметр exclude_booking_id доступен только сотруднику или владельцу записи."""
    if booking_id is None:
        return None
    if auth_svc.is_staff(user) or owner_id == user.id:
        return booking_id
    raise HTTPException(status_code=403, detail="Нет доступа")


async def _owner_of(session: AsyncSession, booking_id: int | None) -> int | None:
    if booking_id is None:
        return None
    b = await svc.get_booking(session, booking_id)
    return b.client_id if b else None


@router.get("/availability/days", response_model=list[DayOut])
async def availability_days(
    service_id: int,
    master_id: int | None = None,
    exclude_booking_id: int | None = None,
    user: User = Depends(auth_svc.get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    exclude = _exclude_for(user, exclude_booking_id, await _owner_of(session, exclude_booking_id))
    return await svc.list_days(
        session, settings, service_id=service_id, master_id=master_id,
        exclude_booking_id=exclude, staff=auth_svc.is_staff(user) and exclude is not None,
    )


@router.get("/slots", response_model=list[SlotOut])
async def slots(
    service_id: int,
    day: date,
    master_id: int | None = None,
    exclude_booking_id: int | None = None,
    user: User = Depends(auth_svc.get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    exclude = _exclude_for(user, exclude_booking_id, await _owner_of(session, exclude_booking_id))
    return await svc.list_slots(
        session, settings, service_id=service_id, master_id=master_id, day=day,
        exclude_booking_id=exclude, staff=auth_svc.is_staff(user) and exclude is not None,
    )


@router.get("/masters/next", response_model=NextAvailableOut)
async def masters_next(
    service_id: int,
    _: User = Depends(auth_svc.get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    return await svc.next_available(session, settings, service_id=service_id)


# ---------------------------------------------------------------- записи клиента

@router.post("/bookings", response_model=BookingOut)
async def create_booking(
    body: BookingCreate,
    background: BackgroundTasks,
    user: User = Depends(auth_svc.get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    booking = await svc.create_booking(
        session,
        settings,
        client=user,
        service_id=body.service_id,
        master_id=body.master_id,
        starts_at=body.starts_at,
        client_name=body.client_name,
        client_phone=body.client_phone,
        comment=body.comment,
    )
    data = _out(booking, settings)
    background.add_task(
        notify.notify_text, user.platform.value, user.platform_user_id,
        notify.booking_confirmed_client(settings, data),
    )
    background.add_task(notify.notify_staff, settings, notify.booking_new_staff(data))
    return data


@router.get("/bookings/mine", response_model=list[BookingOut])
async def my_bookings(
    user: User = Depends(auth_svc.get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    return [_out(b, settings) for b in await svc.my_bookings(session, settings, user.id)]


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
async def cancel_booking(
    booking_id: int,
    background: BackgroundTasks,
    user: User = Depends(auth_svc.get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    booking = await svc.cancel_booking(
        session, settings, booking_id=booking_id, actor=user, staff=False
    )
    data = _out(booking, settings)
    background.add_task(notify.notify_staff, settings, notify.booking_cancelled_staff(data))
    return data


@router.post("/bookings/{booking_id}/reschedule", response_model=BookingOut)
async def client_reschedule(
    booking_id: int,
    body: BookingReschedule,
    background: BackgroundTasks,
    user: User = Depends(auth_svc.get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    booking, old = await svc.reschedule_booking(
        session, settings, booking_id=booking_id, starts_at=body.starts_at,
        master_id=None, staff=False, actor_id=user.id,
    )
    data = _out(booking, settings)
    background.add_task(
        notify.notify_text, user.platform.value, user.platform_user_id,
        notify.booking_rescheduled_client(settings, data, old),
    )
    background.add_task(notify.notify_staff, settings, notify.booking_rescheduled_staff(data, old))
    return data


# ---------------------------------------------------------------- кабинет мастера: записи

@router.get("/staff/day", response_model=list[BookingOut])
async def staff_day(
    day: date,
    master_id: int | None = None,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    rows = await svc.bookings_for_day(session, settings, day=day, master_id=master_id)
    return [_out(b, settings) for b in rows]


@router.get("/staff/day-full")
async def staff_day_full(
    day: date,
    master_id: int | None = None,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Записи дня и доступность мастеров одним запросом."""
    return await _staff_day_full(session, settings, day=day, master_id=master_id)


@router.post("/staff/bookings/{booking_id}/reschedule", response_model=BookingOut)
async def staff_reschedule(
    booking_id: int,
    body: BookingReschedule,
    background: BackgroundTasks,
    staff: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    booking, old = await svc.reschedule_booking(
        session, settings, booking_id=booking_id, starts_at=body.starts_at,
        master_id=body.master_id, staff=True, actor_id=staff.id,
    )
    data = _out(booking, settings)
    client = booking.client
    background.add_task(
        notify.notify_text, client.platform.value, client.platform_user_id,
        notify.booking_rescheduled_client(settings, data, old),
    )
    return data


@router.post("/staff/bookings/{booking_id}/status", response_model=BookingOut)
async def staff_set_status(
    booking_id: int,
    body: StatusUpdate,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    booking = await svc.set_status(session, booking_id, BookingStatus(body.status))
    return _out(booking, settings)


@router.delete("/staff/bookings/{booking_id}")
async def staff_delete_booking(
    booking_id: int,
    background: BackgroundTasks,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    booking = await svc.get_booking(session, booking_id)
    if not booking:
        raise svc.BookingError("Запись не найдена", code="not_found")
    data = _out(booking, settings)
    client = booking.client
    await svc.delete_booking(session, booking_id)
    if data["is_upcoming"]:
        background.add_task(
            notify.notify_text, client.platform.value, client.platform_user_id,
            notify.booking_removed_client(settings, data),
        )
    return {"ok": True}


@router.post("/staff/clients/{client_id}/block")
async def staff_block_client(
    client_id: int,
    background: BackgroundTasks,
    staff: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    result = await svc.set_client_blocked(session, client_id=client_id, blocked=True, actor=staff)
    background.add_task(
        notify.notify_text,
        result["platform"],
        result["platform_user_id"],
        notify.client_blocked(settings),
    )
    return result


@router.post("/staff/clients/{client_id}/unblock")
async def staff_unblock_client(
    client_id: int,
    background: BackgroundTasks,
    staff: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    result = await svc.set_client_blocked(session, client_id=client_id, blocked=False, actor=staff)
    background.add_task(
        notify.notify_text,
        result["platform"],
        result["platform_user_id"],
        notify.client_unblocked(settings),
    )
    return result


# ---------------------------------------------------------------- кабинет мастера: оплата

@router.post("/staff/payments", response_model=PaymentOut)
async def create_payment(
    body: PaymentCreate,
    staff: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    payment = await pay_svc.create_payment(
        session, settings, booking_id=body.booking_id, amount_rub=body.amount_rub,
        created_by=staff, replace=body.replace,
    )
    client = payment.client
    delivered = False
    if payment.qr_path:
        delivered = await notify.notify_photo(
            client.platform.value,
            client.platform_user_id,
            payment.qr_path,
            notify.payment_caption(settings, payment.amount_rub, payment.description),
        )
    return pay_svc.payment_to_out(payment, delivered)


@router.get("/staff/payments/{payment_id}", response_model=PaymentOut)
async def get_payment(
    payment_id: int,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
) -> dict:
    payment = await pay_svc.get_payment(session, payment_id)
    if not payment:
        raise svc.BookingError("Счёт не найден", code="not_found")
    return pay_svc.payment_to_out(payment)


@router.post("/staff/payments/{payment_id}/paid", response_model=PaymentOut)
async def mark_payment_paid(
    payment_id: int,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
) -> dict:
    payment = await pay_svc.mark_paid(session, payment_id)
    return pay_svc.payment_to_out(payment)


# ---------------------------------------------------------------- кабинет мастера: график

@router.get("/staff/availability")
async def staff_availability(
    master_id: int,
    day: date,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    return await svc.list_availability(session, settings, master_id=master_id, day=day)


@router.post("/staff/day-off")
async def staff_set_day_off(
    body: DayOffRequest,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    await svc.set_day_off(
        session, settings, master_id=body.master_id, day=body.day, reason=body.reason, force=body.force
    )
    return {"ok": True}


@router.delete("/staff/day-off")
async def staff_clear_day_off(
    master_id: int,
    day: date,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await svc.clear_day_off(session, master_id=master_id, day=day)
    return {"ok": True}


@router.post("/staff/breaks")
async def staff_add_break(
    body: BreakCreate,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        start, end = time_cls.fromisoformat(body.start_time), time_cls.fromisoformat(body.end_time)
    except ValueError as exc:
        raise svc.BookingError("Некорректное время", code="bad_range") from exc
    row = await svc.add_break(
        session, settings, master_id=body.master_id, day=body.day,
        start_time=start, end_time=end, reason=body.reason, force=body.force,
    )
    return {"ok": True, "id": row.id}


@router.delete("/staff/breaks/{break_id}")
async def staff_delete_break(
    break_id: int,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await svc.delete_break(session, break_id)
    return {"ok": True}


@router.get("/staff/schedule/{master_id}", response_model=WeekSchedule)
async def staff_get_week(
    master_id: int,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    return {"days": await svc.get_week(session, settings, master_id)}


@router.put("/staff/schedule/{master_id}")
async def staff_put_week(
    master_id: int,
    body: WeekSchedule,
    _: User = Depends(auth_svc.require_staff),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        await svc.set_week(session, master_id=master_id, items=[d.model_dump() for d in body.days])
    except ValueError as exc:
        if isinstance(exc, svc.BookingError):
            raise
        raise svc.BookingError("Некорректное время", code="bad_range") from exc
    return {"ok": True}
