"""Точка входа: python bot.py

Читает config.json, поднимает SQLite и крутит long polling.
Никаких зависимостей — только стандартная библиотека Python 3.9+.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time

from kkal.ai import AI
from kkal.api import TelegramAPI, TelegramError
from kkal.db import DB
from kkal.handlers import Bot

log = logging.getLogger("kkal")

COMMANDS = [
    ("start", "меню и справка"),
    ("app", "открыть мини-приложение"),
    ("eat", "записать еду"),
    ("add", "новое блюдо в базу"),
    ("today", "итоги дня"),
    ("dishes", "мои блюда"),
    ("workout", "отметить тренировку"),
    ("stats", "статистика"),
    ("goal", "цели по КБЖУ"),
    ("undo", "отменить последнюю запись"),
    ("export", "выгрузить CSV"),
    ("help", "как пользоваться"),
    ("cancel", "отменить диалог"),
]

CONFIG_PATH = "config.json"

EXAMPLE_CONFIG = {
    "token": "123456789:AA...",
    "owner_id": 0,
    "utc_offset": 4,
    "db": "data/kkal.db",
    "webapp": {"url": "https://kkal.example.com"},
    "ai": {
        "enabled": False,
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "vision_model": "gpt-4o-mini",
        "timeout": 30,
    },
}


def setup_logging(log_file: str = "data/bot.log") -> None:
    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%H:%M:%S", handlers=handlers)


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        print("Файл config.json не найден.\n")
        print("1) Скопируй config.example.json в config.json")
        print("2) Вставь токен от @BotFather")
        print("\nПример config.json:")
        print(json.dumps(EXAMPLE_CONFIG, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    with open(CONFIG_PATH, encoding="utf-8") as fh:
        config = json.load(fh)

    env_token = os.environ.get("KKAL_TOKEN")
    if env_token:
        config["token"] = env_token

    token = str(config.get("token", "")).strip()
    if not token or token.startswith("123456789"):
        print("В config.json не указан токен бота.\n")
        print("Как получить токен:")
        print("1. Открой @BotFather в Telegram")
        print("2. /newbot → придумай имя и username (должен заканчиваться на bot)")
        print("3. Скопируй строку вида 123456789:AAH... в поле \"token\"")
        raise SystemExit(1)

    config.setdefault("owner_id", 0)
    config.setdefault("utc_offset", 4)
    config.setdefault("db", "data/kkal.db")
    return config


def parse_retry_after(text: str, default: int = 5) -> int:
    match = re.search(r"retry after (\d+)", text or "")
    return int(match.group(1)) if match else default


def parse_owner(value) -> tuple[int, str]:
    """owner_id может быть числом (123456789) или username (@robchik69)."""
    raw = str(value or "").strip()
    try:
        return int(raw), ""
    except ValueError:
        return 0, raw.lstrip("@").lower()


def run() -> None:
    setup_logging()
    config = load_config()

    token = str(config["token"]).strip()
    owner_id, owner_username = parse_owner(config.get("owner_id"))
    tz_offset = float(config.get("utc_offset", 4))
    db_path = str(config.get("db", "data/kkal.db"))

    db = DB(db_path)
    api = TelegramAPI(token)
    webapp_url = str((config.get("webapp") or {}).get("url", "")).strip()
    if webapp_url and not webapp_url.startswith("https://"):
        log.warning("webapp.url = %s: Telegram открывает мини-приложения только по https, "
                    "кнопка /app работать не будет.", webapp_url)
        webapp_url = ""
    ai_cfg = config.get("ai") or {}
    try:
        ai = AI(ai_cfg)
    except Exception as exc:  # кривая секция ai не должна мешать боту работать
        log.error("Секция ai в config.json не читается (%s) — работаю без нейросети.", exc)
        ai = None
    bot = Bot(api, db, owner_id=owner_id, tz_offset=tz_offset,
              export_dir=os.path.dirname(db_path) or "data",
              owner_username=owner_username, webapp_url=webapp_url,
              ai=ai)
    if bot.ai and bot.ai.enabled:
        log.info("Нейросеть включена: %s (%s)", bot.ai.model, bot.ai.base_url)
    elif ai_cfg.get("enabled"):
        log.warning("Секция ai.enabled = true, но нет api_key (или адреса локальной модели) — "
                    "фото и разбор текста работать не будут.")

    try:
        me = api._call("getMe")
        log.info("Бот @%s запущен. Часовой пояс: UTC+%s", me.get("username"), tz_offset)
    except TelegramError as exc:
        log.error("Токен не принимается: %s", exc)
        raise SystemExit(1)

    if owner_username:
        log.warning("owner_id задан как @%s: доступ по username работает, но если сменишь "
                    "username — доступ потеряешь. Надёжнее числовой id: отправь боту /id "
                    "и впиши его в config.json.", owner_username)
    elif not owner_id:
        log.warning("owner_id не задан — бот отвечает всем. Узнай свой id командой /id "
                    "и впиши в config.json.")

    api.set_my_commands(COMMANDS)

    if webapp_url:
        try:
            api.set_chat_menu_button(webapp_url)
            log.info("Кнопка меню в чате ведёт на %s", webapp_url)
        except TelegramError as exc:
            log.warning("Не смог поставить кнопку мини-приложения: %s", exc)

    offset = int(db.get_setting("offset", "0") or 0)
    backoff = 2

    while True:
        try:
            updates = api.get_updates(offset)
            backoff = 2
        except TelegramError as exc:
            message = str(exc)
            if exc.code == 409:
                log.error("Конфликт: этот токен уже слушает другой процесс (409). "
                          "Останови второй экземпляр. Пауза 10 с.")
                time.sleep(10)
            elif exc.code == 429:
                wait = parse_retry_after(message)
                log.warning("Лимит запросов, пауза %s с", wait)
                time.sleep(wait)
            elif exc.code in (401, 404):
                log.error("Telegram отверг токен (%s): %s", exc.code, message)
                raise SystemExit(1)
            else:
                log.warning("Сбой связи: %s — повтор через %s с", message, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            continue
        except KeyboardInterrupt:
            raise

        for update in updates:
            offset = int(update["update_id"]) + 1
            db.set_setting("offset", str(offset))
            try:
                bot.handle_update(update)
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("Ошибка обработки апдейта %s", update.get("update_id"))


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        print("\nОстановлено. Данные в data/kkal.db сохранены.")


if __name__ == "__main__":
    main()
