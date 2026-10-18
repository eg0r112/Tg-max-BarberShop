from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models import Booking, BookingStatus, Payment, PaymentStatus, User
from app.services.booking import BookingError, get_booking

QR_DIR = Path(__file__).resolve().parents[2] / "data" / "qr"
QR_DIR.mkdir(parents=True, exist_ok=True)

LIVE_STATUSES = (PaymentStatus.pending, PaymentStatus.sent)


def _clean(value: str) -> str:
    # «|» — разделитель полей ГОСТ-QR, в значениях его быть не должно
    return value.replace("|", " ").strip()


def build_gost_payload(settings: Settings, amount_rub: int, purpose: str) -> str:
    """Платёжный QR по ГОСТ Р 56042 — открывается в приложении банка."""
    if not settings.payments_ready:
        raise BookingError(
            "Реквизиты оплаты не настроены. Заполните PAY_* в файле .env",
            code="payments_not_configured",
        )
    if not re.fullmatch(r"\d{9}", settings.pay_bic.strip()):
        raise BookingError("PAY_BIC должен состоять из 9 цифр", code="payments_not_configured")
    for key, val in (("PAY_PERSONAL_ACC", settings.pay_personal_acc), ("PAY_CORRESP_ACC", settings.pay_corresp_acc)):
        if not re.fullmatch(r"\d{20}", val.strip()):
            raise BookingError(f"{key} должен состоять из 20 цифр", code="payments_not_configured")

    parts = [
        "ST00012",
        f"Name={_clean(settings.pay_name)}",
        f"PersonalAcc={_clean(settings.pay_personal_acc)}",
        f"BankName={_clean(settings.pay_bank_name)}",
        f"BIC={_clean(settings.pay_bic)}",
        f"CorrespAcc={_clean(settings.pay_corresp_acc)}",
        f"Sum={amount_rub * 100}",
        f"Purpose={_clean(purpose)[:160]}",
    ]
    if settings.pay_inn.strip():
        parts.append(f"PayeeINN={_clean(settings.pay_inn)}")
    return "|".join(parts)


def build_payment_payload(settings: Settings, amount_rub: int, payment_id: int, purpose: str) -> str:
    mode = settings.payment_mode
    if mode == "sbp":
        if not settings.sbp_payload_template.strip():
            raise BookingError("SBP_PAYLOAD_TEMPLATE не задан", code="payments_not_configured")
        return settings.sbp_payload_template.format(
            amount_rub=amount_rub,
            amount_kopecks=amount_rub * 100,
            payment_id=payment_id,
            merchant=settings.sbp_merchant_name,
        )
    if mode == "provider":
        raise BookingError(
            "Интеграция с API банка ещё не подключена (PAYMENT_MODE=provider)",
            code="payments_not_configured",
        )
    return build_gost_payload(settings, amount_rub, purpose)


def generate_qr_image(payload: str, filename: str) -> Path:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=3)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    path = QR_DIR / filename
    img.save(path)
    return path


async def get_payment(session: AsyncSession, payment_id: int) -> Payment | None:
    return (
        await session.execute(
            select(Payment).where(Payment.id == payment_id).options(selectinload(Payment.client))
        )
    ).scalar_one_or_none()


async def create_payment(
    session: AsyncSession,
    settings: Settings,
    *,
    booking_id: int,
    amount_rub: int,
    created_by: User,
    replace: bool,
) -> Payment:
    booking = await get_booking(session, booking_id)
    if not booking or booking.status == BookingStatus.cancelled:
        raise BookingError("Запись не найдена", code="not_found")

    existing = [p for p in booking.payments if p.status in (*LIVE_STATUSES, PaymentStatus.paid)]
    paid = [p for p in existing if p.status == PaymentStatus.paid]
    live = [p for p in existing if p.status in LIVE_STATUSES]
    if paid:
        raise BookingError(
            "Эта запись уже оплачена", code="already_paid",
            payment_id=paid[-1].id, amount_rub=paid[-1].amount_rub,
        )
    if not replace:
        if live:
            raise BookingError(
                "Счёт по этой записи уже выставлен", code="payment_exists",
                payment_id=live[-1].id, amount_rub=live[-1].amount_rub,
            )

    description = f"Оплата услуги «{booking.service.name}»"
    payment = Payment(
        client_id=booking.client_id,
        booking_id=booking.id,
        created_by_id=created_by.id,
        amount_rub=amount_rub,
        status=PaymentStatus.pending,
        payload="pending",
        description=description,
        expires_at=datetime.now(ZoneInfo(settings.shop_timezone))
        + timedelta(minutes=settings.payment_ttl_minutes),
    )
    session.add(payment)
    await session.flush()

    try:
        payload = build_payment_payload(
            settings, amount_rub, payment.id, f"{description} #{booking.id}"
        )
        qr_path = generate_qr_image(payload, f"pay_{payment.id}_{uuid.uuid4().hex[:12]}.png")
    except Exception:
        await session.rollback()
        raise

    for old in live:  # прежние неоплаченные счета аннулируются — живой QR только один
        old.status = PaymentStatus.cancelled
    payment.payload = payload
    payment.qr_path = str(qr_path)
    payment.status = PaymentStatus.sent
    await session.commit()
    payment = await get_payment(session, payment.id)  # type: ignore[assignment]
    return payment  # type: ignore[return-value]


async def mark_paid(session: AsyncSession, payment_id: int) -> Payment:
    payment = await get_payment(session, payment_id)
    if not payment:
        raise BookingError("Счёт не найден", code="not_found")
    if payment.status == PaymentStatus.cancelled:
        raise BookingError("Этот счёт аннулирован — выставьте новый", code="cancelled")
    payment.status = PaymentStatus.paid
    payment.paid_at = datetime.now(ZoneInfo("UTC"))
    if payment.booking_id:
        booking = await session.get(Booking, payment.booking_id)
        if booking and booking.status in (BookingStatus.pending, BookingStatus.confirmed):
            booking.status = BookingStatus.completed
    await session.commit()
    return payment


def payment_to_out(payment: Payment, delivered: bool | None = None) -> dict:
    qr_url = None
    if payment.qr_path:
        qr_url = f"/media/qr/{Path(payment.qr_path).name}"
    return {
        "id": payment.id,
        "booking_id": payment.booking_id,
        "amount_rub": payment.amount_rub,
        "status": payment.status.value,
        "description": payment.description,
        "qr_url": qr_url,
        "expires_at": payment.expires_at,
        "client_name": payment.client.full_name if payment.client else None,
        "delivered": delivered,
    }
