from __future__ import annotations

import enum
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Platform(str, enum.Enum):
    telegram = "telegram"
    max = "max"


class BookingStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"
    no_show = "no_show"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    paid = "paid"
    expired = "expired"
    cancelled = "cancelled"


class UserRole(str, enum.Enum):
    client = "client"
    staff = "staff"
    admin = "admin"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("platform", "platform_user_id", name="uq_user_platform"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform), nullable=False)
    platform_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(128))
    full_name: Mapped[str | None] = mapped_column(String(256))
    phone: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.client)
    # Ручное переопределение роли (команда /role в боте — сотрудник смотрит приложение как клиент)
    role_override: Mapped[UserRole | None] = mapped_column(Enum(UserRole), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bookings: Mapped[list[Booking]] = relationship(back_populates="client")
    payments: Mapped[list[Payment]] = relationship(
        back_populates="client",
        foreign_keys="Payment.client_id",
    )


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(128))
    bio: Mapped[str | None] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    bookings: Mapped[list[Booking]] = relationship(back_populates="master")
    schedules: Mapped[list[MasterSchedule]] = relationship(back_populates="master")
    time_offs: Mapped[list[MasterTimeOff]] = relationship(back_populates="master")
    breaks: Mapped[list["MasterBreak"]] = relationship(back_populates="master")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64), default="Другое", server_default="Другое")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    price_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    bookings: Mapped[list[Booking]] = relationship(back_populates="service")


class MasterSchedule(Base):
    """weekday: 0=Mon ... 6=Sun"""

    __tablename__ = "master_schedules"
    __table_args__ = (UniqueConstraint("master_id", "weekday", name="uq_master_weekday"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    is_day_off: Mapped[bool] = mapped_column(Boolean, default=False)

    master: Mapped[Master] = relationship(back_populates="schedules")


class MasterTimeOff(Base):
    """Полный выходной мастера на конкретную дату."""

    __tablename__ = "master_time_offs"
    __table_args__ = (UniqueConstraint("master_id", "day", name="uq_master_day_off"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(256))

    master: Mapped[Master] = relationship(back_populates="time_offs")


class MasterBreak(Base):
    """Перерыв / занятость мастера в конкретный день (слоты на это время недоступны)."""

    __tablename__ = "master_breaks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(256))

    master: Mapped[Master] = relationship(back_populates="breaks")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(Enum(BookingStatus), default=BookingStatus.confirmed)
    client_name: Mapped[str] = mapped_column(String(256), nullable=False)
    client_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    reminded_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    reminded_2h: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    client: Mapped[User] = relationship(back_populates="bookings")
    master: Mapped[Master] = relationship(back_populates="bookings")
    service: Mapped[Service] = relationship(back_populates="bookings")
    payments: Mapped[list[Payment]] = relationship(back_populates="booking")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    amount_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.pending)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    qr_path: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(String(256))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    client: Mapped[User] = relationship(
        back_populates="payments",
        foreign_keys=[client_id],
    )
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    booking: Mapped[Booking | None] = relationship(back_populates="payments")
