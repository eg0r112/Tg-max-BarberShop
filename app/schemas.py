from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

PlatformName = Literal["telegram", "max"]
RoleName = Literal["client", "staff", "admin"]


class AuthRequest(BaseModel):
    platform: PlatformName
    init_data: str = ""
    # Только для DEV_MODE (проверка в браузере)
    user_id: int | None = None
    username: str | None = None
    full_name: str | None = None


class AuthResponse(BaseModel):
    token: str
    user_id: int
    platform: PlatformName
    platform_user_id: int
    role: RoleName
    full_name: str | None = None
    phone: str | None = None
    needs_name: bool = False
    is_blocked: bool = False


class ProfileUpdate(BaseModel):
    full_name: str = Field(min_length=2, max_length=64)


class ShopInfo(BaseModel):
    name: str
    phone: str
    address: str
    timezone: str
    today: date
    workday_start: str
    workday_end: str
    horizon_days: int
    cancel_min_hours: int
    payments_ready: bool
    dev_mode: bool


class ServiceOut(BaseModel):
    id: int
    name: str
    category: str
    description: str | None
    duration_minutes: int
    price_rub: int

    model_config = {"from_attributes": True}


class MasterOut(BaseModel):
    id: int
    name: str
    title: str | None
    bio: str | None

    model_config = {"from_attributes": True}


class DayOut(BaseModel):
    day: date
    free: int


class SlotOut(BaseModel):
    starts_at: datetime
    ends_at: datetime
    label: str
    master_ids: list[int]


class NextMasterOut(BaseModel):
    master_id: int
    starts_at: datetime | None


class NextAvailableOut(BaseModel):
    masters: list[NextMasterOut]
    any: datetime | None


class BookingCreate(BaseModel):
    service_id: int
    master_id: int | None = None  # None = любой свободный мастер
    starts_at: datetime
    client_name: str = Field(min_length=1, max_length=128)
    client_phone: str = Field(min_length=5, max_length=32)
    comment: str | None = Field(default=None, max_length=500)


class BookingReschedule(BaseModel):
    starts_at: datetime
    master_id: int | None = None


class PaymentSummary(BaseModel):
    id: int
    status: str
    amount_rub: int


class BookingOut(BaseModel):
    id: int
    service_id: int
    service_name: str
    master_id: int
    master_name: str
    starts_at: datetime
    ends_at: datetime
    status: str
    client_name: str
    client_phone: str
    client_user_id: int
    price_rub: int
    duration_minutes: int
    comment: str | None = None
    is_upcoming: bool
    can_modify: bool
    payment: PaymentSummary | None = None


class StatusUpdate(BaseModel):
    status: Literal["no_show", "completed", "confirmed"]


class PaymentCreate(BaseModel):
    booking_id: int
    amount_rub: int = Field(gt=0, le=1_000_000)
    replace: bool = False


class PaymentOut(BaseModel):
    id: int
    booking_id: int | None
    amount_rub: int
    status: str
    description: str | None
    qr_url: str | None
    expires_at: datetime | None
    client_name: str | None = None
    delivered: bool | None = None


class DayOffRequest(BaseModel):
    master_id: int
    day: date
    reason: str | None = Field(default=None, max_length=200)
    force: bool = False


class BreakCreate(BaseModel):
    master_id: int
    day: date
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    reason: str | None = Field(default=None, max_length=200)
    force: bool = False


class WeekDay(BaseModel):
    weekday: int = Field(ge=0, le=6)
    is_day_off: bool
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class WeekSchedule(BaseModel):
    days: list[WeekDay]
