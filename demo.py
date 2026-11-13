"""Демо без Telegram: показывает, как выглядят экраны бота.

Запуск:  python demo.py
Ничего не отправляет в сеть и не трогает data/kkal.db — работает в data/_demo.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import timedelta

from kkal import fmt
from kkal.db import DB
from kkal.handlers import Bot

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CHAT = 1


class ConsoleAPI:
    """Вместо Telegram печатает сообщения в консоль."""

    def __init__(self) -> None:
        self._mid = 0

    def _show(self, text: str, kb: dict | None) -> dict:
        self._mid += 1
        print("─" * 46)
        print(text)
        rows = (kb or {}).get("inline_keyboard") or []
        if rows:
            print()
            for row in rows:
                print("  " + "  ".join(f"[{b['text']}]" for b in row))
        print()
        return {"message_id": self._mid}

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        return self._show(text, reply_markup)

    def edit_message(self, chat_id: int, message_id: int, text: str,
                     reply_markup: dict | None = None) -> None:
        self._show(text, reply_markup)

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        pass

    def send_document(self, chat_id: int, path: str, caption: str = "") -> None:
        print(f"📎 отправлен файл {path}")

    def set_my_commands(self, commands) -> None:
        pass


def main() -> None:
    folder = os.path.join("data", "_demo")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    db = DB(os.path.join(folder, "demo.db"))
    bot = Bot(ConsoleAPI(), db, owner_id=CHAT, tz_offset=4, export_dir=folder)

    today = fmt.today(4)
    yesterday = (fmt.now(4) - timedelta(days=1)).strftime("%Y-%m-%d")

    print("\n>>> python demo.py — демонстрация экранов. Это не настоящий Telegram.\n")

    print("Заполняю демо-базу: 5 блюд, завтрак и обед, цель 2200 ккал…\n")
    sys.stdout.flush()
    db.add_or_update_dish("Сырники", 220, 18, 9, 24)
    db.add_or_update_dish("Гречка отварная", 110, 4, 1.3, 21)
    db.add_or_update_dish("Куриная грудка", 165, 31, 3.6, 0)
    db.add_or_update_dish("Овсянка на молоке", 130, 4.2, 3, 21)
    db.add_or_update_dish("Кофе с молоком", 40, 1.5, 1.5, 4)

    db.log_meal(today, "08:10", "Овсянка на молоке", 325, 10.5, 7.5, 52.5, grams=250)
    db.log_meal(today, "08:15", "Кофе с молоком", 80, 3, 3, 8, grams=200)
    db.log_meal(today, "13:40", "Гречка отварная", 220, 8, 2.6, 42, grams=200)
    db.log_meal(today, "13:45", "Куриная грудка", 247.5, 46.5, 5.4, 0, grams=150)
    db.set_workout(yesterday, True, "ноги, 45 мин")
    db.set_goal(2200, 150, 70, 220)

    def msg(text: str) -> None:
        bot.handle_update({
            "update_id": 1,
            "message": {"message_id": 1, "chat": {"id": CHAT}, "text": text},
        })

    def cb(data: str) -> None:
        bot.handle_update({
            "update_id": 2,
            "callback_query": {"id": "cb", "data": data,
                               "message": {"message_id": 5, "chat": {"id": CHAT}}},
        })

    msg("/today")
    cb("m:stats")
    msg("/eat сырники 150")
    cb("m:workout")
    msg("/start")

    db.close()
    shutil.rmtree(folder, ignore_errors=True)
    print("Готово. Демо-база удалена.")


if __name__ == "__main__":
    main()
