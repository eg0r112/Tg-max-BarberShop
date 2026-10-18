from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Platform, User, UserRole

logger = logging.getLogger(__name__)

TOKEN_TTL = 60 * 60 * 24 * 14  # 14 дней


# ---------------------------------------------------------------- initData

def _data_check_string(parsed: dict[str, str]) -> str:
    return "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))


def _verify_webapp_hash(init_data: str, bot_token: str) -> dict[str, str]:
    """Общая проверка подписи initData (Telegram и MAX используют схему WebAppData)."""
    if not init_data or not bot_token:
        raise HTTPException(status_code=401, detail="Не удалось подтвердить пользователя")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Не удалось подтвердить пользователя")

    check = _data_check_string(parsed).encode()
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret, check, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        # запасной вариант: токен бота как прямой ключ HMAC
        alt = hmac.new(bot_token.encode(), check, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(alt, received_hash):
            raise HTTPException(status_code=401, detail="Не удалось подтвердить пользователя")
    return parsed


def _check_age(parsed: dict[str, str], max_age: int) -> None:
    try:
        auth_date = int(parsed.get("auth_date", "0"))
    except ValueError:
        auth_date = 0
    if auth_date and time.time() - auth_date > max_age:
        raise HTTPException(status_code=401, detail="Сессия устарела, откройте приложение заново")


def validate_telegram_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict:
    parsed = _verify_webapp_hash(init_data, bot_token)
    _check_age(parsed, max_age)
    user = json.loads(parsed.get("user", "{}") or "{}")
    if "id" not in user:
        raise HTTPException(status_code=401, detail="Не удалось определить пользователя")
    return {
        "platform_user_id": int(user["id"]),
        "username": user.get("username"),
        "full_name": " ".join(filter(None, [user.get("first_name"), user.get("last_name")])).strip() or None,
    }


def validate_max_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict:
    parsed = _verify_webapp_hash(init_data, bot_token)
    _check_age(parsed, max_age)

    user_raw = parsed.get("user")
    if user_raw:
        user = json.loads(user_raw)
        uid = user.get("id") or user.get("user_id")
        if uid is None:
            raise HTTPException(status_code=401, detail="Не удалось определить пользователя")
        name = user.get("name") or " ".join(
            filter(None, [user.get("first_name"), user.get("last_name")])
        ).strip()
        return {
            "platform_user_id": int(uid),
            "username": user.get("username"),
            "full_name": name or None,
        }
    if "user_id" in parsed:
        return {
            "platform_user_id": int(parsed["user_id"]),
            "username": parsed.get("username"),
            "full_name": parsed.get("name"),
        }
    raise HTTPException(status_code=401, detail="Не удалось определить пользователя")


# ---------------------------------------------------------------- токены (без состояния)

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(settings: Settings, user: User) -> str:
    payload = json.dumps({"uid": user.id, "exp": int(time.time()) + TOKEN_TTL}).encode()
    body = _b64(payload)
    sig = _b64(hmac.new(settings.resolved_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def parse_token(settings: Settings, token: str) -> int:
    try:
        body, sig = token.split(".", 1)
        expected = _b64(hmac.new(settings.resolved_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise ValueError("bad signature")
        payload = json.loads(_unb64(body))
        if payload["exp"] < time.time():
            raise ValueError("expired")
        return int(payload["uid"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Сессия истекла") from exc


# ---------------------------------------------------------------- пользователи и роли

def resolve_role(settings: Settings, platform: Platform, platform_user_id: int) -> UserRole:
    if platform == Platform.telegram:
        if platform_user_id in settings.admin_tg:
            return UserRole.admin
        if platform_user_id in settings.staff_tg:
            return UserRole.staff
    else:
        if platform_user_id in settings.admin_max:
            return UserRole.admin
        if platform_user_id in settings.staff_max:
            return UserRole.staff
    return UserRole.client


def configured_role(settings: Settings, user: User) -> UserRole:
    """Роль по настройкам (.env), без учёта ручного переопределения."""
    return resolve_role(settings, user.platform, user.platform_user_id)


def effective_role(settings: Settings, user: User) -> UserRole:
    """Роль, с которой пользователь реально работает: override (если задан) или роль из настроек."""
    return user.role_override or configured_role(settings, user)


async def set_role_override(
    session: AsyncSession, settings: Settings, *, platform: Platform, platform_user_id: int,
    override: UserRole | None,
) -> User:
    """Включает/снимает ручное переопределение роли. Доступно только тем, кто по настройкам сотрудник/админ."""
    user = await upsert_user(
        session, settings, platform=platform, platform_user_id=platform_user_id, username=None, full_name=None
    )
    if configured_role(settings, user) == UserRole.client:
        raise PermissionError("Переключение роли доступно только сотрудникам")
    user.role_override = override
    user.role = effective_role(settings, user)
    await session.commit()
    await session.refresh(user)
    return user


async def upsert_user(
    session: AsyncSession,
    settings: Settings,
    *,
    platform: Platform,
    platform_user_id: int,
    username: str | None,
    full_name: str | None,
) -> User:
    role = resolve_role(settings, platform, platform_user_id)
    result = await session.execute(
        select(User).where(User.platform == platform, User.platform_user_id == platform_user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        # Имя клиент указывает сам в приложении; для сотрудников берём из Telegram/MAX.
        user = User(
            platform=platform,
            platform_user_id=platform_user_id,
            username=username,
            full_name=full_name if role != UserRole.client else None,
            role=role,
        )
        session.add(user)
    else:
        user.username = username or user.username
        if role != UserRole.client and full_name and not user.full_name:
            user.full_name = full_name
        if role == UserRole.client:
            user.role_override = None  # исключили из сотрудников — переопределение теряет смысл
        user.role = effective_role(settings, user)  # роль из настроек (можно повысить/понизить) или override
    await session.commit()
    await session.refresh(user)
    return user


def needs_name(user: User) -> bool:
    """Клиенту нужно указать имя, если ещё не сохранил его в профиле."""
    if user.role != UserRole.client:
        return False
    return not (user.full_name and user.full_name.strip())


def raise_if_blocked(user: User) -> None:
    if user.is_blocked and user.role == UserRole.client:
        raise HTTPException(
            status_code=403,
            detail={"code": "blocked", "message": "Доступ ограничен. Обратитесь в салон."},
        )


async def authenticate(
    session: AsyncSession,
    settings: Settings,
    *,
    platform: str,
    init_data: str,
    user_id: int | None = None,
    username: str | None = None,
    full_name: str | None = None,
) -> tuple[User, str]:
    plat = Platform(platform)

    if init_data:
        if plat == Platform.telegram:
            data = validate_telegram_init_data(init_data, settings.telegram_bot_token)
        else:
            data = validate_max_init_data(init_data, settings.max_bot_token)
        platform_user_id = data["platform_user_id"]
        username = data.get("username") or username
        full_name = data.get("full_name") or full_name
    else:
        if not settings.dev_mode:
            raise HTTPException(
                status_code=401,
                detail="Откройте приложение через бота в Telegram или MAX",
            )
        if not user_id:
            raise HTTPException(status_code=401, detail="Требуется вход")
        platform_user_id = user_id

    user = await upsert_user(
        session,
        settings,
        platform=plat,
        platform_user_id=platform_user_id,
        username=username,
        full_name=full_name,
    )
    return user, issue_token(settings, user)


async def update_profile(session: AsyncSession, user: User, *, full_name: str) -> User:
    raise_if_blocked(user)
    name = " ".join(full_name.split())
    if len(name) < 2:
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_name", "message": "Укажите имя (минимум 2 символа)"},
        )
    if len(name) > 64:
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_name", "message": "Имя слишком длинное"},
        )
    user.full_name = name
    await session.commit()
    await session.refresh(user)
    return user


async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется вход")
    uid = parse_token(settings, authorization.removeprefix("Bearer ").strip())
    user = await session.get(User, uid)
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    if configured_role(settings, user) == UserRole.client and user.role_override:
        user.role_override = None
    role = effective_role(settings, user)
    if role != user.role:
        user.role = role
        await session.commit()
    raise_if_blocked(user)
    return user


async def require_staff(user: User = Depends(get_current_user)) -> User:
    if user.role not in (UserRole.staff, UserRole.admin):
        raise HTTPException(status_code=403, detail="Доступ только для сотрудников")
    return user


def is_staff(user: User) -> bool:
    return user.role in (UserRole.staff, UserRole.admin)


def auth_payload(user: User, token: str) -> dict:
    return {
        "token": token,
        "user_id": user.id,
        "platform": user.platform.value,
        "platform_user_id": user.platform_user_id,
        "role": user.role.value,
        "full_name": user.full_name,
        "phone": user.phone,
        "needs_name": needs_name(user),
        "is_blocked": bool(user.is_blocked),
    }
