"""Форматирование чисел, дат и прогресс-баров для сообщений бота."""

from __future__ import annotations

import html
from datetime import date as _date
from datetime import datetime, timedelta, timezone

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def now(tz_offset: float = 4.0) -> datetime:
    """Текущее локальное время при фиксированном смещении (МСК+1 = 4)."""
    return datetime.now(timezone(timedelta(hours=tz_offset))).replace(tzinfo=None)


def today(tz_offset: float = 4.0) -> str:
    return now(tz_offset).strftime("%Y-%m-%d")


def parse_date(value: str) -> _date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def human_date(date_str: str, today_str: str | None = None) -> str:
    """'2026-11-12' -> '12 ноя, ср' (или 'сегодня')."""
    d = parse_date(date_str)
    if today_str and date_str == today_str:
        return "сегодня"
    return f"{d.day} {MONTHS[d.month - 1]}, {WEEKDAYS[d.weekday()]}"


def short_date(date_str: str) -> str:
    """'2026-11-12' -> '12.11'."""
    d = parse_date(date_str)
    return f"{d.day:02d}.{d.month:02d}"


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def num(value: float | None) -> str:
    """Компактное число: 1240, 42,5, 3,25."""
    if value is None:
        return "—"
    value = float(value)
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    text = f"{value:.1f}"
    return text.replace(".", ",")


def kcal(value: float | None) -> str:
    """Калории показываем целыми: 872,5 -> 873."""
    if value is None:
        return "—"
    return str(int(round(float(value))))


def bar(current: float, goal: float | None, width: int = 10) -> str:
    if not goal:
        return ""
    ratio = max(0.0, current / goal)
    filled = min(width, int(round(ratio * width)))
    return "▰" * filled + "▱" * (width - filled)


def progress_line(label: str, current: float, goal: float | None, unit: str = "") -> str:
    """Строка вида: 'Ккал  ▰▰▰▱▱  1240 / 2200  ·  осталось 960'."""
    if not goal:
        return f"{label} <b>{num(current)}</b>{(' ' + unit) if unit else ''}"
    left = goal - current
    tail = f"осталось {num(left)}" if left >= 0 else f"⚠️ перебор {num(-left)}"
    return f"{label} {bar(current, goal)} <b>{num(current)}</b> / {num(goal)}  ·  {tail}"


def parse_number(text: str) -> float | None:
    """'150', '150 г', '1,5', '2.5 ккал' -> float или None."""
    cleaned = text.strip().lower().replace(",", ".")
    for suffix in ("ккал", "гр", "г", "g", "kcal"):
        cleaned = cleaned.replace(suffix, "")
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None
