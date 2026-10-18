from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- салон ---
    shop_name: str = "Акварель"
    shop_timezone: str = "Europe/Moscow"
    shop_phone: str = ""
    shop_address: str = ""

    public_base_url: str = "http://127.0.0.1:8000"
    database_url: str = "sqlite+aiosqlite:///./data/akvarel.db"
    # CORS для фронта на другом домене (GitHub Pages). Через запятую или *
    cors_origins: str = "*"

    # --- безопасность ---
    # Пусто = ключ генерируется один раз и хранится в data/secret.key
    secret_key: str = ""
    # Вход без initData (для проверки в браузере). В проде держать false!
    dev_mode: bool = False

    # --- боты ---
    telegram_bot_token: str = ""
    telegram_webapp_url: str = "http://127.0.0.1:8000/app/"

    max_bot_token: str = ""
    max_webapp_url: str = "http://127.0.0.1:8000/app/"
    max_api_base: str = "https://platform-api2.max.ru"

    staff_telegram_ids: str = ""
    staff_max_ids: str = ""
    admin_telegram_ids: str = ""
    admin_max_ids: str = ""

    # --- оплата ---
    # gost — QR по реквизитам (ГОСТ Р 56042), sbp — шаблон ссылки СБП, provider — API банка
    payment_mode: Literal["gost", "sbp", "provider"] = "gost"
    sbp_payload_template: str = ""
    sbp_merchant_name: str = "Акварель"
    payment_ttl_minutes: int = 30

    pay_name: str = ""
    pay_personal_acc: str = ""
    pay_bank_name: str = ""
    pay_bic: str = ""
    pay_corresp_acc: str = ""
    pay_inn: str = ""

    # --- правила записи ---
    workday_start: str = "10:00"
    workday_end: str = "20:00"
    slot_minutes: int = 30
    booking_horizon_days: int = 30
    min_lead_minutes: int = 60          # нельзя записаться ближе чем через N минут
    cancel_min_hours: int = 2           # клиент может отменить/перенести не позднее чем за N часов
    max_active_bookings: int = 5        # активных будущих записей на клиента
    reminders_enabled: bool = True

    host: str = "0.0.0.0"
    port: int = 8000

    @staticmethod
    def parse_ids(raw: str) -> set[int]:
        result: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                result.add(int(part))
        return result

    @property
    def staff_tg(self) -> set[int]:
        return self.parse_ids(self.staff_telegram_ids) | self.parse_ids(self.admin_telegram_ids)

    @property
    def staff_max(self) -> set[int]:
        return self.parse_ids(self.staff_max_ids) | self.parse_ids(self.admin_max_ids)

    @property
    def admin_tg(self) -> set[int]:
        return self.parse_ids(self.admin_telegram_ids)

    @property
    def admin_max(self) -> set[int]:
        return self.parse_ids(self.admin_max_ids)

    @property
    def payments_ready(self) -> bool:
        if self.payment_mode == "sbp":
            return bool(self.sbp_payload_template.strip())
        if self.payment_mode == "provider":
            return False
        return all(
            v.strip()
            for v in (
                self.pay_name,
                self.pay_personal_acc,
                self.pay_bank_name,
                self.pay_bic,
                self.pay_corresp_acc,
            )
        )

    def resolved_secret(self) -> bytes:
        if self.secret_key.strip():
            return self.secret_key.strip().encode()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "secret.key"
        if not path.exists():
            path.write_text(secrets.token_hex(32), encoding="utf-8")
        return path.read_text(encoding="utf-8").strip().encode()


@lru_cache
def get_settings() -> Settings:
    return Settings()
