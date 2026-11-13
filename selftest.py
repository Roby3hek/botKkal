"""Автотест без сети: гоняет сценарии через поддельный Telegram API.

Запуск:  python selftest.py
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import timedelta

from kkal.db import DB
from kkal.handlers import Bot

CHAT = 777
OWNER = 777

# Консоль Windows по умолчанию cp1251 — переключаем вывод на UTF-8,
# иначе русский текст и эмодзи роняют печать.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class FakeAPI:
    """Записывает всё, что бот хотел отправить, но никуда не ходит."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.keyboards: list[dict | None] = []
        self.documents: list[str] = []
        self._mid = 1000

    def _remember(self, text: str, kb: dict | None) -> None:
        self.texts.append(text)
        self.keyboards.append(kb)

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        self._mid += 1
        self._remember(text, reply_markup)
        return {"message_id": self._mid}

    def edit_message(self, chat_id: int, message_id: int, text: str,
                     reply_markup: dict | None = None) -> None:
        self._remember(text, reply_markup)

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        pass

    def send_document(self, chat_id: int, path: str, caption: str = "") -> None:
        self.documents.append(path)

    def get_file(self, file_id: str) -> str:
        return "photos/" + file_id + ".jpg"

    def download_file(self, file_path: str, timeout: float = 60.0) -> bytes:
        return b"\xff\xd8\xff\xe0" + file_path.encode("utf-8")

    def set_my_commands(self, commands) -> None:
        pass

    # ---- помощники для теста

    @property
    def last(self) -> str:
        return self.texts[-1] if self.texts else ""

    def all_text(self) -> str:
        return "\n".join(self.texts)


class FakeAI:
    """Подменяет нейросеть: отдаёт заранее заготовленные блюда."""

    def __init__(self, items: list[dict] | None = None, error: Exception | None = None) -> None:
        self.enabled = True
        self.items = items or []
        self.error = error
        self.calls: list[tuple[int, str]] = []

    def analyze_photo(self, image_bytes: bytes, dishes=None, mime: str = "image/jpeg") -> list[dict]:
        self.calls.append((len(image_bytes), mime))
        if self.error:
            raise self.error
        return [dict(item) for item in self.items]


FAILS: list[str] = []


def check(condition: bool, label: str) -> None:
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}")
        FAILS.append(label)


def check_in(needle: str, haystack: str, label: str) -> None:
    check(needle in haystack, f"{label} (ищем {needle!r})")


class Harness:
    def __init__(self, folder: str) -> None:
        self.db = DB(os.path.join(folder, "test.db"))
        self.api = FakeAPI()
        self.bot = Bot(self.api, self.db, owner_id=OWNER, tz_offset=4, export_dir=folder)
        self.uid = 0

    def msg(self, text: str, chat_id: int = CHAT) -> None:
        self.uid += 1
        self.bot.handle_update({
            "update_id": self.uid,
            "message": {"message_id": self.uid, "chat": {"id": chat_id}, "text": text},
        })

    def cb(self, data: str, chat_id: int = CHAT) -> None:
        self.uid += 1
        self.bot.handle_update({
            "update_id": self.uid,
            "callback_query": {
                "id": f"cb{self.uid}",
                "data": data,
                "message": {"message_id": 42, "chat": {"id": chat_id}},
            },
        })

    def reset(self) -> None:
        self.api.texts.clear()
        self.api.keyboards.clear()


def scenario_basic(h: Harness) -> None:
    print("\n1. Старт и меню")
    h.msg("/start")
    check_in("Дневник КБЖУ", h.api.last, "/start отвечает приветствием")
    check(bool(h.api.keyboards[-1]), "/start присылает клавиатуру")

    print("\n2. Диалог добавления блюда")
    h.msg("/add")
    check_in("Как назвать", h.api.last, "спрашивает название")
    h.msg("Сырники")
    check_in("ккал на 100 г", h.api.last, "спрашивает калорийность")
    h.msg("220 18 9 24")
    check_in("Сырники", h.api.last, "блюдо создано")
    dish = h.db.find_dish_by_name("Сырники")
    check(dish is not None, "блюдо есть в базе")
    check(dish is not None and abs(dish["kcal"] - 220) < 0.01, "калорийность 220")
    check(dish is not None and abs(dish["carb"] - 24) < 0.01, "углеводы 24")

    print("\n3. Запись еды по граммам")
    h.msg("/e сырники 150")
    check_in("330", h.api.last, "150 г сырников = 330 ккал")
    totals = h.db.totals_for_date(_today(h))
    check(abs(totals["kcal"] - 330) < 0.01, "в дневнике 330 ккал")
    check(abs(totals["protein"] - 27) < 0.01, "в дневнике 27 г белка")

    print("\n4. Экран дня")
    h.msg("/today")
    check_in("Сырники", h.api.last, "блюдо в списке за день")
    check_in("330", h.api.last, "сумма за день видна")

    print("\n5. Тренировка")
    h.cb("m:workout")
    check_in("не отмечена", h.api.last, "по умолчанию тренировки нет")
    h.cb("w:y")
    check_in("была", h.api.last, "тренировка отмечена")
    day = h.db.get_day(_today(h))
    check(day is not None and day["workout"] == 1, "флаг тренировки в базе")

    print("\n6. Заметка к тренировке")
    h.cb("w:note")
    h.msg("ноги, 45 мин")
    day = h.db.get_day(_today(h))
    check(day is not None and day["note"] == "ноги, 45 мин", "заметка сохранена")

    print("\n7. Статистика")
    h.msg("/stats")
    check_in("Статистика", h.api.last, "статистика открывается")
    check_in("330", h.api.last, "калории за день в статистике")

    print("\n8. Отмена записи")
    h.msg("/undo")
    totals = h.db.totals_for_date(_today(h))
    check(totals["kcal"] == 0, "запись удалена")

    print("\n9. Однострочное добавление")
    h.msg("/add Гречка 110 4 1 21")
    dish = h.db.find_dish_by_name("Гречка")
    check(dish is not None and abs(dish["kcal"] - 110) < 0.01, "Гречка 110 ккал создана")

    print("\n10. Запись через кнопки")
    dish = h.db.find_dish_by_name("Гречка")
    assert dish is not None
    h.cb(f"d:{dish['id']}")
    check_in("Сколько грамм", h.api.last, "спрашивает вес")
    h.cb(f"g:{dish['id']}:200")
    check_in("220", h.api.last, "200 г гречки = 220 ккал")
    check(abs(h.db.totals_for_date(_today(h))["kcal"] - 220) < 0.01, "в дневнике 220 ккал")

    print("\n11. Свой вес через диалог")
    h.cb(f"gw:{dish['id']}")
    h.msg("180")
    check_in("198", h.api.last, "180 г гречки = 198 ккал")
    check(abs(h.db.totals_for_date(_today(h))["kcal"] - 418) < 0.01, "сумма 418 ккал")

    print("\n12. Цель")
    h.msg("/goal 2200 150 70 220")
    goal = h.db.get_goal()
    check(goal is not None and abs(goal["kcal"] - 2200) < 0.01, "цель сохранена")
    h.msg("/today")
    check_in("осталось", h.api.last, "показывает остаток до цели")
    check("▰" in h.api.last or "▱" in h.api.last, "прогресс-бар нарисован")

    print("\n13. Разовая запись без карточки")
    h.cb("q")
    check_in("Что съел", h.api.last, "спрашивает название")
    h.msg("Шоколад")
    check_in("ккал", h.api.last, "спрашивает калории")
    h.msg("320 25 12 30")
    check_in("320", h.api.last, "разовая запись сохранена")
    meal = h.db.last_meal(_today(h))
    check(meal is not None and meal["dish_id"] is None, "у разовой записи нет карточки")

    print("\n14. Список блюд и страницы")
    for i in range(6):
        h.db.add_or_update_dish(f"Тестовое блюдо {i}", 100 + i, 5, 3, 10)
    h.msg("/dishes")
    check_in("Мои блюда", h.api.last, "список блюд открывается")
    h.cb("dp:1")
    check_in("стр. 2", h.api.last, "переключение страниц работает")

    print("\n15. Удаление блюда")
    dish = h.db.find_dish_by_name("Тестовое блюдо 3")
    assert dish
    h.cb(f"dd:{dish['id']}")
    check(h.db.find_dish_by_name("Тестовое блюдо 3") is None, "блюдо удалено")

    print("\n16. Поиск")
    h.msg("/find греч")
    check_in("Гречка", h.api.last, "поиск по части названия")

    print("\n17. Экспорт CSV")
    h.msg("/export")
    check(len(h.api.documents) == 1, "файл отправлен")
    if h.api.documents:
        check(os.path.exists(h.api.documents[0]), "CSV создан на диске")
        with open(h.api.documents[0], encoding="utf-8-sig") as fh:
            head = fh.readline()
        check("ккал" in head, "в CSV есть колонка ккал")

    print("\n18. Отмена диалога")
    h.msg("/add")
    h.msg("/cancel")
    check_in("Ок", h.api.last, "/cancel сбрасывает диалог")
    h.msg("просто текст")
    check_in("Не понял", h.api.last, "вне диалога текст не ломает бота")

    print("\n19. Валидация ввода")
    h.msg("/add")
    h.msg("Кривое блюдо")
    h.msg("много")
    check_in("Нужно число", h.api.last, "нечисло отвергнуто")
    h.msg("9000")
    check_in("Нужно число", h.api.last, "9000 ккал отвергнуто")
    h.msg("120")
    check_in("Белки", h.api.last, "после ккал спрашивает белки")
    h.msg("-")
    h.msg("-")
    h.msg("15")
    dish = h.db.find_dish_by_name("Кривое блюдо")
    check(dish is not None and dish["kcal"] == 120 and dish["carb"] == 15, "блюдо с прочерками создано")

    print("\n20. Чужой человек не пройдёт")
    h.reset()
    h.msg("/start", chat_id=12345)
    check_in("личный бот", h.api.last, "посторонний получает отказ")
    check(h.db.get_goal() is not None, "но данные не пострадали")


def scenario_empty(h: Harness) -> None:
    print("\n21. Чистая база")
    h.msg("/today")
    check_in("Пока пусто", h.api.last, "пустой день подсказывает, что делать")
    h.msg("/stats")
    check_in("Записей нет", h.api.last, "пустая статистика не падает")
    h.msg("/undo")
    check_in("нечего отменять", h.api.last, "undo на пустом дне")
    h.msg("/dishes")
    check_in("Пока пусто", h.api.last, "пустая база блюд")
    h.msg("/export")
    check_in("нечего выгружать", h.api.last, "экспорт на пустой базе")


def scenario_long(h: Harness) -> None:
    """Проверяем длинные периоды: разбивка по неделям и счёт тренировок."""
    print("\n21. Длинный период (30 дней и всё время)")
    from kkal import fmt
    today = _today(h)
    day = fmt.parse_date(today)
    dish = h.db.find_dish_by_name("Сырники")

    for offset in range(0, 21):
        date_str = (day - timedelta(days=offset)).isoformat()
        h.db.log_meal(date_str, "12:00", "Сырники", 330, 27, 13.5, 36,
                      grams=150, dish_id=dish["id"] if dish else None)
        if offset % 3 == 0:
            h.db.set_workout(date_str, True)

    h.cb("s:30")
    check_in("По неделям", h.api.last, "30 дней показываются по неделям")
    check_in("ккал/день", h.api.last, "в недельной строке есть среднее")
    check_in("Тренировок отмечено", h.api.last, "тренировки посчитаны")
    check("," not in h.api.last.split("ккал/день")[0].split(":")[-1],
          "среднее по неделе без лишних десятых")

    h.cb("s:0")
    check_in("за всё время", h.api.last, "режим «всё время» работает")
    check_in("По неделям", h.api.last, "«всё время» тоже по неделям")


def scenario_owner(h: Harness) -> None:
    """Владельца можно задать как id, так и @username — частая ошибка в конфиге."""
    print("\n22. Владелец задан ником")
    api = FakeAPI()
    bot = Bot(api, h.db, owner_id="@robchik69", tz_offset=4, export_dir="data")

    def send(chat_id: int, text: str, username: str | None = None) -> None:
        message = {"message_id": 1, "chat": {"id": chat_id}, "text": text}
        if username:
            message["from"] = {"id": chat_id, "username": username}
        bot.handle_update({"update_id": 1, "message": message})

    send(777, "/start")
    check_in("личный бот", api.last, "без username доступа нет")

    send(777, "/start", "robchik69")
    check_in("Дневник КБЖУ", api.last, "@username владельца пускает")

    api.texts.clear()
    send(777, "/start", "ROBCHIK69")
    check_in("Дневник КБЖУ", api.last, "ник в верхнем регистре тоже подходит")

    api.texts.clear()
    send(777, "/start", "чужой_ник")
    check_in("личный бот", api.last, "чужой ник не пускает")

    api.texts.clear()
    send(777, "/id", "robchik69")
    check_in("777", api.last, "/id показывает chat_id")


def scenario_app(h: Harness) -> None:
    """Кнопка мини-приложения: без адреса — подсказка, с адресом — web_app-кнопка."""
    print("\n23. Мини-приложение")
    api = FakeAPI()
    bot = Bot(api, h.db, owner_id=OWNER, tz_offset=4, export_dir="data")
    bot.handle_update({"update_id": 1, "message": {"message_id": 1, "chat": {"id": CHAT},
                                                  "text": "/app"}})
    check_in("webapp", api.last, "без адреса объясняет, что вписать в конфиг")
    check("web_app" not in repr(api.keyboards[-1]), "без адреса не присылает web_app-кнопку")

    api.texts.clear()
    bot.webapp_url = "https://kkal.example.com"
    bot.handle_update({"update_id": 2, "message": {"message_id": 2, "chat": {"id": CHAT},
                                                  "text": "/app"}})
    kb = api.keyboards[-1] or {}
    button = (kb.get("inline_keyboard") or [[{}]])[0][0]
    check(button.get("web_app", {}).get("url") == "https://kkal.example.com",
          "кнопка ведёт на адрес из конфига")
    check_in("мини-приложении", api.last, "текст про мини-приложение")

    api.texts.clear()
    bot.handle_update({"update_id": 3, "message": {"message_id": 3, "chat": {"id": CHAT},
                                                  "text": "/start"}})
    check_in("/app", api.last, "в /start появилась подсказка про /app")


def scenario_photo(h: Harness) -> None:
    """Фото еды прямо в чате: без ИИ — подсказка, с ИИ — черновик и запись по кнопке."""
    from kkal import fmt
    from kkal.ai import AIError

    print("\n24. Фото еды в чате")
    today = fmt.today(h.bot.tz)
    before = len(h.db.meals_for_date(today))
    before_kcal = h.db.totals_for_date(today)["kcal"]

    def photo(api: FakeAPI, bot: Bot, size: int = 100) -> None:
        bot.handle_update({"update_id": 90, "message": {
            "message_id": 90, "chat": {"id": CHAT},
            "photo": [{"file_id": "small", "width": 90, "height": 90, "file_size": 10},
                      {"file_id": "big", "width": 1280, "height": 960, "file_size": size}],
        }})

    # без нейросети бот честно говорит, что фото не разбирает
    api = FakeAPI()
    bot = Bot(api, h.db, owner_id=OWNER, tz_offset=4, export_dir="data")
    photo(api, bot)
    check_in("нейросеть выключена", api.last, "без ИИ объясняет, что фото не разбирает")
    check(len(h.db.meals_for_date(today)) == before, "без ИИ ничего не записал")

    # нейросеть вернула два блюда: одно есть в базе, второе — разово
    items = [
        {"name": "Сырники", "grams": 150, "kcal": 330, "protein": 27, "fat": 13.5,
         "carb": 36, "source": "ai", "note": "две штуки"},
        {"name": "Кофе с молоком", "grams": 200, "kcal": 40, "protein": 2, "fat": 2,
         "carb": 3, "source": "ai"},
    ]
    ai = FakeAI(items)
    api = FakeAPI()
    bot = Bot(api, h.db, owner_id=OWNER, tz_offset=4, export_dir="data", ai=ai)
    photo(api, bot)
    check(ai.calls and ai.calls[0][1] == "image/jpeg", "в нейросеть уходит самое крупное фото")
    check_in("Что вижу на фото", api.all_text(), "показывает разбор фото")
    check_in("Сырники", api.last, "в разборе есть блюдо из базы")
    check_in("есть в базе", api.last, "помечает знакомое блюдо")
    check_in("две штуки", api.last, "показывает комментарий модели")
    check_in("370", api.last, "итого по фото 370 ккал")
    check(any(b.get("callback_data") == "ai:save"
              for row in (api.keyboards[-1] or {}).get("inline_keyboard", []) for b in row),
          "есть кнопка «Записать всё»")
    check(len(h.db.meals_for_date(today)) == before, "до подтверждения ничего не записано")

    # отмена черновика
    api.texts.clear()
    h.bot = bot
    h.cb("ai:no")
    check_in("ничего не записал", api.last, "кнопка «Отмена» выбрасывает черновик")
    check(len(h.db.meals_for_date(today)) == before, "после отмены записей нет")

    # подтверждение: всё пишется одной пачкой
    photo(api, bot)
    h.cb("ai:save")
    check_in("Записал с фото", api.last, "кнопка «Записать всё» пишет еду")
    meals = h.db.meals_for_date(today)
    check(len(meals) == before + 2, "появились две записи")
    after_kcal = h.db.totals_for_date(today)["kcal"]
    check(abs(after_kcal - before_kcal - 370) < 0.01, "в итоги добавилось 330 + 40 ккал")
    dish = h.db.find_dish_by_name("Сырники")
    matched = [m for m in meals if m["name"] == "Сырники"]
    check(bool(matched) and matched[-1]["dish_id"] == dish["id"],
          "знакомое блюдо привязано к базе")
    check(bool(matched) and abs(matched[-1]["kcal"] - 330) < 0.01,
          "КБЖУ знакомого блюда пересчитано по граммам")
    free = [m for m in meals if m["name"] == "Кофе с молоком"]
    check(bool(free) and free[-1]["dish_id"] is None, "незнакомое блюдо записано разово")
    check(not bot.states.get(CHAT), "черновик после записи забыт")

    # пустой ответ и сбой модели
    api.texts.clear()
    h.bot = Bot(api, h.db, owner_id=OWNER, tz_offset=4, export_dir="data", ai=FakeAI([]))
    photo(api, h.bot)
    check_in("не вижу еды", api.last, "пустой разбор не превращается в запись")

    api.texts.clear()
    h.bot = Bot(api, h.db, owner_id=OWNER, tz_offset=4, export_dir="data",
                ai=FakeAI(error=AIError("модель недоступна")))
    photo(api, h.bot)
    check_in("Не получилось разобрать", api.last, "сбой модели объясняется, бот жив")
    check(len(h.db.meals_for_date(today)) == before + 2, "после сбоя лишних записей нет")


def _today(h: Harness) -> str:
    from kkal import fmt
    return fmt.today(h.bot.tz)


def main() -> int:
    folder = os.path.join("data", "_selftest")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    try:
        h = Harness(folder)
        scenario_empty(h)
        scenario_basic(h)
        scenario_long(h)
        scenario_owner(h)
        scenario_app(h)
        scenario_photo(h)
        h.db.close()
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    print()
    if FAILS:
        print(f"Провалено проверок: {len(FAILS)}")
        for label in FAILS:
            print(f"  - {label}")
        return 1
    print("Все проверки прошли ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
