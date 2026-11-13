"""Хранилище на SQLite: блюда, приёмы пищи, пометки тренировок, цели."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS dishes (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    kcal        REAL    NOT NULL,
    protein     REAL    NOT NULL DEFAULT 0,
    fat         REAL    NOT NULL DEFAULT 0,
    carb        REAL    NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    last_used   TEXT,
    use_count   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meals (
    id          INTEGER PRIMARY KEY,
    date        TEXT    NOT NULL,        -- YYYY-MM-DD, локальная дата
    time        TEXT    NOT NULL,        -- HH:MM
    dish_id     INTEGER,                 -- NULL для разовой записи
    name        TEXT    NOT NULL,        -- снимок названия
    grams       REAL,                    -- NULL для разовой записи
    kcal        REAL    NOT NULL,
    protein     REAL    NOT NULL DEFAULT 0,
    fat         REAL    NOT NULL DEFAULT 0,
    carb        REAL    NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(date);

CREATE TABLE IF NOT EXISTS days (
    date        TEXT PRIMARY KEY,
    workout     INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY,
    kcal        REAL    NOT NULL,
    protein     REAL    NOT NULL DEFAULT 0,
    fat         REAL    NOT NULL DEFAULT 0,
    carb        REAL    NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: str = "data/kkal.db"):
        self.path = path
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ блюда

    def all_dishes(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM dishes ORDER BY name COLLATE NOCASE"))

    def get_dish(self, dish_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM dishes WHERE id = ?", (dish_id,)).fetchone()

    def find_dish_by_name(self, name: str) -> sqlite3.Row | None:
        target = name.strip().lower()
        for row in self.conn.execute("SELECT * FROM dishes"):
            if row["name"].lower() == target:
                return row
        return None

    def add_or_update_dish(
        self,
        name: str,
        kcal: float,
        protein: float = 0.0,
        fat: float = 0.0,
        carb: float = 0.0,
    ) -> tuple[int, bool]:
        """Возвращает (id, created). Если имя уже есть — обновляет цифры."""
        name = " ".join(name.split())
        existing = self.find_dish_by_name(name)
        stamp = datetime.now().isoformat(timespec="seconds")
        if existing:
            self.conn.execute(
                "UPDATE dishes SET name = ?, kcal = ?, protein = ?, fat = ?, carb = ? WHERE id = ?",
                (name, kcal, protein, fat, carb, existing["id"]),
            )
            self.conn.commit()
            return existing["id"], False
        cur = self.conn.execute(
            "INSERT INTO dishes (name, kcal, protein, fat, carb, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, kcal, protein, fat, carb, stamp),
        )
        self.conn.commit()
        return int(cur.lastrowid), True

    def delete_dish(self, dish_id: int) -> None:
        self.conn.execute("DELETE FROM dishes WHERE id = ?", (dish_id,))
        self.conn.commit()

    def recent_dishes(self, limit: int = 8) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM dishes WHERE last_used IS NOT NULL "
                "ORDER BY last_used DESC, id DESC LIMIT ?",
                (limit,),
            )
        )

    def popular_dishes(self, limit: int = 8) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM dishes ORDER BY use_count DESC, last_used DESC, id DESC LIMIT ?",
                (limit,),
            )
        )

    def search_dishes(self, query: str) -> list[sqlite3.Row]:
        """Поиск по подстроке; точное совпадение и начало слова — выше."""
        q = query.strip().lower()
        if not q:
            return []
        scored: list[tuple[int, sqlite3.Row]] = []
        for row in self.conn.execute("SELECT * FROM dishes"):
            name = row["name"].lower()
            if name == q:
                score = 100
            elif name.startswith(q):
                score = 80
            elif q in name:
                score = 60
            elif all(word in name for word in q.split()):
                score = 40
            else:
                continue
            scored.append((score + min(int(row["use_count"]), 20), row))
        scored.sort(key=lambda item: (-item[0], item[1]["name"].lower()))
        return [row for _, row in scored]

    def touch_dish(self, dish_id: int, stamp: str) -> None:
        self.conn.execute(
            "UPDATE dishes SET use_count = use_count + 1, last_used = ? WHERE id = ?",
            (stamp, dish_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------ приёмы пищи

    def log_meal(
        self,
        date: str,
        time: str,
        name: str,
        kcal: float,
        protein: float = 0.0,
        fat: float = 0.0,
        carb: float = 0.0,
        grams: float | None = None,
        dish_id: int | None = None,
    ) -> int:
        stamp = datetime.now().isoformat(timespec="seconds")
        cur = self.conn.execute(
            "INSERT INTO meals (date, time, dish_id, name, grams, kcal, protein, fat, carb, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (date, time, dish_id, name, grams, kcal, protein, fat, carb, stamp),
        )
        if dish_id is not None:
            self.touch_dish(dish_id, stamp)
        self.conn.commit()
        return int(cur.lastrowid)

    def meals_for_date(self, date: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM meals WHERE date = ? ORDER BY time, id",
                (date,),
            )
        )

    def totals_for_date(self, date: str) -> dict[str, float]:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(kcal), 0) AS kcal, COALESCE(SUM(protein), 0) AS protein, "
            "COALESCE(SUM(fat), 0) AS fat, COALESCE(SUM(carb), 0) AS carb "
            "FROM meals WHERE date = ?",
            (date,),
        ).fetchone()
        return {"kcal": row["kcal"], "protein": row["protein"], "fat": row["fat"], "carb": row["carb"]}

    def meals_between(self, start: str, end: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM meals WHERE date BETWEEN ? AND ? ORDER BY date, time, id",
                (start, end),
            )
        )

    def last_meal(self, date: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM meals WHERE date = ? ORDER BY id DESC LIMIT 1", (date,)
        ).fetchone()

    def delete_meal(self, meal_id: int) -> None:
        self.conn.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
        self.conn.commit()

    def first_meal_date(self) -> str | None:
        row = self.conn.execute("SELECT MIN(date) AS d FROM meals").fetchone()
        return row["d"]

    def all_meals(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM meals ORDER BY date, time, id"))

    # ------------------------------------------------------------ дни

    def get_day(self, date: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM days WHERE date = ?", (date,)).fetchone()

    def set_workout(self, date: str, workout: bool, note: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO days (date, workout, note) VALUES (?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET workout = excluded.workout, "
            "note = COALESCE(excluded.note, days.note)",
            (date, 1 if workout else 0, note),
        )
        self.conn.commit()

    def set_day_note(self, date: str, note: str) -> None:
        self.conn.execute(
            "INSERT INTO days (date, workout, note) VALUES (?, 0, ?) "
            "ON CONFLICT(date) DO UPDATE SET note = excluded.note",
            (date, note),
        )
        self.conn.commit()

    def days_between(self, start: str, end: str) -> dict[str, sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM days WHERE date BETWEEN ? AND ?", (start, end)
        )
        return {row["date"]: row for row in rows}

    # ------------------------------------------------------------ цели

    def set_goal(self, kcal: float, protein: float, fat: float, carb: float) -> None:
        self.conn.execute("DELETE FROM goals")
        self.conn.execute(
            "INSERT INTO goals (kcal, protein, fat, carb, updated_at) VALUES (?, ?, ?, ?, ?)",
            (kcal, protein, fat, carb, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def get_goal(self) -> dict[str, float] | None:
        row = self.conn.execute("SELECT * FROM goals ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        return {"kcal": row["kcal"], "protein": row["protein"], "fat": row["fat"], "carb": row["carb"]}

    # ------------------------------------------------------------ настройки

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()
