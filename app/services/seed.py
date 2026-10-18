from __future__ import annotations

from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Master, MasterSchedule, Service

# (категория, название, описание, минуты, цена, порядок)
DEFAULT_SERVICES = [
    ("Стрижки", "Женская стрижка", "Мытьё, стрижка, лёгкая укладка", 60, 2500, 10),
    ("Стрижки", "Женская стрижка (длинные волосы)", "Волосы ниже лопаток", 75, 3000, 11),
    ("Стрижки", "Мужская стрижка", "Машинка и ножницы, оформление формы", 45, 1500, 20),
    ("Стрижки", "Мужская стрижка + борода", "Стрижка и оформление бороды", 60, 2200, 21),
    ("Стрижки", "Детская стрижка", "До 12 лет", 30, 1000, 30),
    ("Стрижки", "Чёлка", "Подравнивание чёлки", 15, 500, 31),
    ("Укладки", "Укладка повседневная", "Фен и брашинг", 40, 1500, 40),
    ("Укладки", "Укладка вечерняя", "Локоны, объём, праздничный образ", 60, 2500, 41),
    ("Укладки", "Причёска на выпускной", "Сложная вечерняя укладка", 90, 4000, 42),
    ("Окрашивание", "Окрашивание в один тон", "Корни и длина, без сложных техник", 120, 4500, 50),
    ("Окрашивание", "Тонирование", "Освежение оттенка", 90, 3500, 51),
    ("Окрашивание", "Мелирование", "Классическое мелирование", 150, 5500, 52),
    ("Окрашивание", "Балаяж / шатуш", "Плавный переход цвета", 180, 7500, 53),
    ("Окрашивание", "Эйр тач", "Сложное мелирование с сохранением длины", 210, 9000, 54),
    ("Окрашивание", "Сложное окрашивание", "Стоимость уточняется на консультации", 180, 8000, 55),
    ("Окрашивание", "Снятие цвета", "Без последующего окрашивания", 90, 3000, 56),
    ("Брови и ресницы", "Окрашивание бровей", "Краска или хна", 20, 600, 60),
    ("Брови и ресницы", "Оформление бровей", "Коррекция формы", 20, 700, 61),
    ("Брови и ресницы", "Окрашивание ресниц", "Краска", 20, 500, 62),
    ("Уход", "Уход / маска", "Восстановление после услуги", 30, 1200, 70),
    ("Уход", "Кератиновое выпрямление", "Длина и состояние — по консультации", 180, 7000, 71),
    ("Уход", "Ботокс для волос", "Плотность и блеск", 120, 5500, 72),
    ("Уход", "Полировка волос", "Срез посечённых кончиков", 45, 2000, 73),
    ("Консультация", "Консультация стилиста", "Подбор формы и цвета", 20, 0, 80),
]

DEFAULT_MASTERS = [
    ("Анна", "топ-стилист", "Стрижки, укладки, окрашивание"),
    ("Мария", "колорист", "Сложное окрашивание и уход"),
    ("Елена", "мастер", "Женские и мужские стрижки"),
]

# переименования ранних названий в каталоге
RENAMES = {
    "AirTouch": "Эйр тач",
    "Женская стрижка (длинные)": "Женская стрижка (длинные волосы)",
    "Снятие цвета / декапирование": "Снятие цвета",
}


async def sync_catalog(session: AsyncSession, settings: Settings) -> None:
    """Приводит каталог услуг и мастеров к актуальному виду (идемпотентно)."""
    for old, new in RENAMES.items():
        row = (await session.execute(select(Service).where(Service.name == old))).scalar_one_or_none()
        exists_new = (await session.execute(select(Service).where(Service.name == new))).scalar_one_or_none()
        if row and not exists_new:
            row.name = new

    for category, name, desc, duration, price, order in DEFAULT_SERVICES:
        row = (await session.execute(select(Service).where(Service.name == name))).scalar_one_or_none()
        if row is None:
            session.add(
                Service(
                    category=category, name=name, description=desc,
                    duration_minutes=duration, price_rub=price, sort_order=order, is_active=True,
                )
            )
        elif not row.category or row.category == "Другое":
            # цены/длительность и активность не трогаем — их правит салон
            row.category = category

    start = time.fromisoformat(settings.workday_start)
    end = time.fromisoformat(settings.workday_end)
    has_masters = (await session.execute(select(Master.id).limit(1))).first() is not None
    if not has_masters:
        for idx, (name, title, bio) in enumerate(DEFAULT_MASTERS):
            master = Master(name=name, title=title, bio=bio, sort_order=idx, is_active=True)
            session.add(master)
            await session.flush()
            for weekday in range(7):
                session.add(
                    MasterSchedule(
                        master_id=master.id, weekday=weekday, start_time=start, end_time=end,
                        is_day_off=weekday == 6,
                    )
                )

    await session.commit()
