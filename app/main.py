from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.bots.runner import BotRuntime
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.services.booking import BookingError
from app.services.seed import sync_catalog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("akvarel")

ROOT = Path(__file__).resolve().parent
MINIAPP = ROOT / "miniapp"
QR_DIR = ROOT.parent / "data" / "qr"
QR_DIR.mkdir(parents=True, exist_ok=True)

ERROR_STATUS = {
    "not_found": 404,
    "forbidden": 403,
    "blocked": 403,
    "slot_taken": 409,
    "client_overlap": 409,
    "has_bookings": 409,
    "payment_exists": 409,
    "already_paid": 409,
    "cancelled": 409,
    "inactive": 409,
    "payments_not_configured": 503,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db()
    async with SessionLocal() as session:
        await sync_catalog(session, settings)

    if settings.dev_mode:
        logger.warning("DEV_MODE=true: вход без подтверждения Telegram/MAX разрешён. Для продакшена отключите!")
    if not settings.payments_ready:
        logger.warning("Реквизиты оплаты не заполнены — выставление QR-счетов недоступно")

    runtime = BotRuntime(settings)
    await runtime.start()
    logger.info("Акварель запущена · мини-приложение: /app/")
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="Акварель — запись и оплата", lifespan=lifespan)

_settings = get_settings()
_origins = [o.strip() for o in (_settings.cors_origins or "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins if _origins != ["*"] else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Кэшируем preflight: браузер не шлёт OPTIONS перед каждым запросом (Chrome ограничит 2 ч)
    max_age=86400,
)
app.include_router(api_router)


@app.exception_handler(BookingError)
async def booking_error_handler(_: Request, exc: BookingError) -> JSONResponse:
    status = ERROR_STATUS.get(exc.code, 400)
    return JSONResponse(
        status_code=status,
        content={"detail": {"code": exc.code, "message": str(exc), **exc.extra}},
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": {"code": "validation", "message": "Проверьте введённые данные"}},
    )


@app.exception_handler(Exception)
async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": {"code": "server_error", "message": "Что-то пошло не так. Попробуйте ещё раз."}},
    )


@app.middleware("http")
async def no_cache_for_app(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/app"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/app/assets", StaticFiles(directory=MINIAPP / "assets"), name="assets")
app.mount("/app/css", StaticFiles(directory=MINIAPP / "css"), name="css")
app.mount("/app/js", StaticFiles(directory=MINIAPP / "js"), name="js")
app.mount("/media/qr", StaticFiles(directory=QR_DIR), name="qr")


@app.get("/app/")
@app.get("/app/{path:path}")
async def miniapp(path: str = "") -> FileResponse:
    return FileResponse(MINIAPP / "index.html")


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
