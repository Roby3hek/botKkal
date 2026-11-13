"""Вся логика бота: команды, кнопки, диалоги ввода, отчёты."""

from __future__ import annotations

import csv
import logging
import os
from collections import defaultdict
from datetime import timedelta

from . import fmt
from .db import DB

log = logging.getLogger(__name__)

MAIN_MENU = {
    "inline_keyboard": [
        [
            {"text": "➕ Записать еду", "callback_data": "m:eat"},
            {"text": "📊 Сегодня", "callback_data": "m:today"},
        ],
        [
            {"text": "🍱 Мои блюда", "callback_data": "m:dishes"},
            {"text": "🏋️ Тренировка", "callback_data": "m:workout"},
        ],
        [
            {"text": "📈 Статистика", "callback_data": "m:stats"},
            {"text": "⚙️ Цели", "callback_data": "m:goal"},
        ],
    ]
}

BACK_MENU = {"inline_keyboard": [[{"text": "⬅️ В меню", "callback_data": "m:menu"}]]}


def _cut(text: str, limit: int = 24) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _to_int(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _norm_user(value) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _photo_file(message: dict) -> tuple[str, str] | None:
    """file_id и mime фото из сообщения: сжатое фото или картинка файлом."""
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        best = max(photos, key=lambda p: (p.get("width", 0) * p.get("height", 0),
                                          p.get("file_size", 0)))
        if best.get("file_id"):
            return str(best["file_id"]), "image/jpeg"
    doc = message.get("document") or {}
    mime = str(doc.get("mime_type") or "")
    if doc.get("file_id") and mime.startswith("image/"):
        return str(doc["file_id"]), mime
    return None


class Bot:
    def __init__(self, api, db: DB, owner_id=0, tz_offset: float = 4.0, export_dir: str = "data",
                 owner_username: str = "", webapp_url: str = "", ai=None):
        self.api = api
        self.db = db
        self.ai = ai  # необязательный kkal.ai.AI: без него фото не разбирается
        # Владельца можно задать числовым id или @username — в конфиге люди путают эти вещи.
        raw = str(owner_id).strip() if owner_id is not None else ""
        self.owner_id = _to_int(raw)
        self.owner_username = ""
        if not self.owner_id and raw not in ("", "0", "None"):
            self.owner_username = _norm_user(raw)
        if owner_username:
            self.owner_username = _norm_user(owner_username)
        self.tz = tz_offset
        self.export_dir = export_dir
        self.webapp_url = str(webapp_url or "").strip()
        self.states: dict[int, dict] = {}

    # ============================================================ приём апдейтов

    def handle_update(self, update: dict) -> None:
        try:
            if "message" in update:
                self._on_message(update["message"])
            elif "callback_query" in update:
                self._on_callback(update["callback_query"])
        except Exception:
            log.exception("Не удалось обработать апдейт %s", update.get("update_id"))

    def _allowed(self, chat_id: int, user: dict | None = None) -> bool:
        if not self.owner_id and not self.owner_username:
            return True
        if self.owner_id and chat_id == self.owner_id:
            return True
        if user:
            if self.owner_id and user.get("id") == self.owner_id:
                return True
            if self.owner_username and _norm_user(user.get("username")) == self.owner_username:
                return True
        self.api.send_message(chat_id, "🔒 Это личный бот одного человека.")
        return False

    # ============================================================ сообщения

    def _on_message(self, message: dict) -> None:
        chat_id = message["chat"]["id"]
        user = message.get("from") or {}
        if not self._allowed(chat_id, user):
            return

        text = (message.get("text") or "").strip()
        if not text:
            photo = _photo_file(message)
            if photo:
                self._on_photo(chat_id, photo[0], photo[1])
                return
            self.api.send_message(
                chat_id,
                "Пока я понимаю текст, кнопки и фото еды — голос появится позже.",
                MAIN_MENU,
            )
            return

        if text.startswith("/"):
            command, _, args = text.partition(" ")
            command = command[1:].split("@")[0].lower()
            self._on_command(chat_id, command, args.strip(), user)
            return

        state = self.states.get(chat_id)
        if state:
            self._on_state_text(chat_id, state, text)
            return

        self.api.send_message(chat_id, "Не понял. Нажми кнопку или /help.", MAIN_MENU)

    def _on_command(self, chat_id: int, command: str, args: str, user: dict | None = None) -> None:
        if command in ("start", "help", "menu"):
            self.states.pop(chat_id, None)
            self.api.send_message(chat_id, self._start_text(), MAIN_MENU)
        elif command in ("app", "mini", "web", "webapp"):
            self._cmd_app(chat_id)
        elif command in ("today", "day"):
            self._show_today(chat_id)
        elif command in ("eat", "e", "log"):
            self._cmd_eat(chat_id, args)
        elif command in ("add",):
            self._cmd_add(chat_id, args)
        elif command in ("dishes", "list", "db"):
            self._show_dishes(chat_id)
        elif command in ("undo", "u", "del"):
            self._undo(chat_id)
        elif command in ("workout", "w", "gym"):
            self._cmd_workout(chat_id, args)
        elif command in ("stats", "stat", "s"):
            period = 7
            if args in ("7", "30", "all", "0"):
                period = 0 if args in ("all", "0") else int(args)
            self._show_stats(chat_id, period)
        elif command in ("goal", "goals", "g"):
            self._cmd_goal(chat_id, args)
        elif command in ("find", "search", "f"):
            self._cmd_find(chat_id, args)
        elif command in ("export", "csv"):
            self._export(chat_id)
        elif command in ("id", "whoami"):
            user = user or {}
            self.api.send_message(
                chat_id,
                f"Твой chat_id: <code>{chat_id}</code>\n"
                f"username: @{fmt.esc(user.get('username') or '—')}\n\n"
                "Впиши chat_id в поле \"owner_id\" в config.json — это надёжнее, "
                "чем username.",
                MAIN_MENU,
            )
        elif command in ("cancel", "c"):
            self.states.pop(chat_id, None)
            self.api.send_message(chat_id, "Ок, отменил.", MAIN_MENU)
        else:
            self.api.send_message(chat_id, f"Не знаю команду /{command}. Список — /help.", MAIN_MENU)

    # ============================================================ кнопки

    def _on_callback(self, cb: dict) -> None:
        message = cb.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")
        data = cb.get("data") or ""
        if chat_id is None:
            return
        self.api.answer_callback(cb["id"])
        if not self._allowed(chat_id, cb.get("from") or {}):
            return

        kind, _, rest = data.partition(":")
        try:
            if kind == "m":
                self._menu_action(chat_id, message_id, rest)
            elif kind == "d":
                self._pick_dish(chat_id, message_id, int(rest))
            elif kind == "g":
                dish_id, _, grams = rest.partition(":")
                self._log_dish(chat_id, message_id, int(dish_id), float(grams))
            elif kind == "gw":
                self._ask_weight(chat_id, int(rest))
            elif kind == "w":
                self._workout_action(chat_id, message_id, rest)
            elif kind == "s":
                self._show_stats(chat_id, int(rest), message_id)
            elif kind == "u":
                self._undo(chat_id, message_id)
            elif kind == "x":
                self.states.pop(chat_id, None)
                self._edit(chat_id, message_id, self._start_text(), MAIN_MENU)
            elif kind == "dp":
                self._show_dishes(chat_id, int(rest), message_id)
            elif kind == "dd":
                self._delete_dish(chat_id, int(rest), message_id)
            elif kind == "del":
                self._show_delete_list(chat_id, message_id)
            elif kind == "add":
                self._start_add(chat_id)
            elif kind == "q":
                self._start_quick(chat_id)
            elif kind == "srch":
                self._ask_search(chat_id)
            elif kind == "gz":
                self._start_goal(chat_id)
            elif kind == "ai":
                self._photo_action(chat_id, message_id, rest)
            else:
                log.warning("Неизвестная кнопка: %s", data)
        except Exception:
            log.exception("Ошибка в обработчике кнопки %s", data)
            self.api.send_message(chat_id, "⚠️ Что-то пошло не так. Попробуй ещё раз.")

    def _menu_action(self, chat_id: int, message_id: int | None, action: str) -> None:
        if action == "menu":
            self.states.pop(chat_id, None)
            self._edit(chat_id, message_id, self._start_text(), MAIN_MENU)
        elif action == "today":
            self._edit(chat_id, message_id, self._today_text(), self._today_kb())
        elif action == "eat":
            self.states.pop(chat_id, None)
            self._edit(chat_id, message_id, self._pick_text(), self._pick_kb())
        elif action == "dishes":
            self._show_dishes(chat_id, 0, message_id)
        elif action == "workout":
            self._edit(chat_id, message_id, self._workout_text(), self._workout_kb())
        elif action == "stats":
            self._show_stats(chat_id, 7, message_id)
        elif action == "goal":
            self._edit(chat_id, message_id, self._goal_text(), self._goal_kb())

    # ============================================================ экраны

    def _start_text(self) -> str:
        today = fmt.today(self.tz)
        goal = self.db.get_goal()
        dish_count = len(self.db.all_dishes())
        lines = [
            "<b>Дневник КБЖУ</b>",
            "",
            f"Блюд в базе: {dish_count}",
        ]
        if goal:
            lines.append(f"Цель: {fmt.num(goal['kcal'])} ккал · Б {fmt.num(goal['protein'])} · "
                         f"Ж {fmt.num(goal['fat'])} · У {fmt.num(goal['carb'])}")
        else:
            lines.append("Цель не задана: /goal 2200 150 70 220")
        lines += [
            "",
            "<b>Как пользоваться</b>",
            "➕ <b>Записать еду</b> — выбрать блюдо или ввести разово",
            "🍱 <b>Мои блюда</b> — база с КБЖУ на 100 г",
            "🏋️ <b>Тренировка</b> — пометить день",
            "📈 <b>Статистика</b> — дни, средние, сравнение тренировок",
            "",
            "<b>Команды</b>",
            "/eat — записать еду",
            "/add — новое блюдо",
            "/today — день и итоги",
            "/stats — статистика",
            "/undo — отменить последнюю запись",
            "/export — выгрузить CSV",
        ]
        if self._ai_on:
            lines += ["", "📷 Пришли <b>фото еды</b> — посчитаю КБЖУ и запишу по кнопке."]
        if self.webapp_url:
            lines += ["", "📱 Мини-приложение с графиками: /app"]
        return "\n".join(lines)

    def _cmd_app(self, chat_id: int) -> None:
        if not self.webapp_url:
            self.api.send_message(
                chat_id,
                "Мини-приложение пока не настроено.\n\n"
                "Впиши адрес в config.json:\n"
                '<code>"webapp": {"url": "https://kkal.example.com"}</code>\n'
                "и перезапусти server.py — потом снова /app.",
                MAIN_MENU,
            )
            return
        self.states.pop(chat_id, None)
        self.api.send_message(
            chat_id,
            "📱 <b>Дневник в мини-приложении</b>\n\n"
            "Тот же дневник, только с графиками, поиском по базе и ИИ. "
            "Открывается поверх чата, данные общие с ботом.",
            {"inline_keyboard": [[{"text": "Открыть дневник",
                                   "web_app": {"url": self.webapp_url}}]]},
        )

    def _today_kb(self) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "➕ Добавить", "callback_data": "m:eat"},
                    {"text": "↩️ Отменить", "callback_data": "u"},
                ],
                [
                    {"text": "🏋️ Тренировка", "callback_data": "m:workout"},
                    {"text": "📈 Статистика", "callback_data": "m:stats"},
                ],
            ]
        }

    def _today_text(self) -> str:
        today = fmt.today(self.tz)
        meals = self.db.meals_for_date(today)
        day = self.db.get_day(today)
        goal = self.db.get_goal()
        totals = self.db.totals_for_date(today)

        weekday = fmt.WEEKDAYS[fmt.parse_date(today).weekday()]
        lines = [f"<b>📊 Сегодня, {fmt.short_date(today)} ({weekday})</b>", ""]

        if day and day["workout"]:
            note = f" · {fmt.esc(day['note'])}" if day["note"] else ""
            lines.append(f"🏋️ Тренировка: <b>была</b>{note}")
        else:
            lines.append("🏋️ Тренировка: не отмечена")

        lines.append("")
        if goal:
            lines.append(fmt.progress_line("Ккал ", round(totals["kcal"]), goal["kcal"]))
            lines.append(fmt.progress_line("Белки", totals["protein"], goal["protein"]))
            lines.append(fmt.progress_line("Жиры ", totals["fat"], goal["fat"]))
            lines.append(fmt.progress_line("Угл. ", totals["carb"], goal["carb"]))
        else:
            lines.append(f"Ккал: <b>{fmt.kcal(totals['kcal'])}</b> · Б {fmt.num(totals['protein'])} · "
                         f"Ж {fmt.num(totals['fat'])} · У {fmt.num(totals['carb'])}")
            lines.append("")
            lines.append("Цель: /goal 2200 150 70 220")

        lines += ["", "<b>Приёмы пищи</b>"]
        if not meals:
            lines.append("Пока пусто — добавь первый приём.")
        else:
            for meal in meals:
                grams = f" — {fmt.num(meal['grams'])} г" if meal["grams"] else ""
                lines.append(f"{meal['time']} · {fmt.esc(meal['name'])}{grams} — "
                             f"<b>{fmt.kcal(meal['kcal'])}</b> ккал")

        return "\n".join(lines)

    def _show_today(self, chat_id: int, message_id: int | None = None) -> None:
        text, kb = self._today_text(), self._today_kb()
        if message_id:
            self._edit(chat_id, message_id, text, kb)
        else:
            self.api.send_message(chat_id, text, kb)

    # ------------------------------------------------------------ выбор блюда

    def _pick_text(self) -> str:
        dishes = self.db.recent_dishes(8) or self.db.popular_dishes(8)
        if not dishes:
            return ("<b>Что съел?</b>\n\nВ базе пока нет блюд.\n"
                    "Сначала добавь блюдо с КБЖУ на 100 г — или запиши разово, без карточки.")
        return "<b>Что съел?</b>\n\nНедавние блюда — нажми, чтобы записать:"

    def _pick_kb(self) -> dict:
        dishes = self.db.recent_dishes(8) or self.db.popular_dishes(8)
        rows: list[list[dict]] = []
        pair: list[dict] = []
        for dish in dishes:
            pair.append({"text": _cut(dish["name"]), "callback_data": f"d:{dish['id']}"})
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        rows.append([
            {"text": "🔍 Найти", "callback_data": "srch"},
            {"text": "⚡ Разово", "callback_data": "q"},
        ])
        rows.append([
            {"text": "➕ Новое блюдо", "callback_data": "add"},
            {"text": "⬅️ В меню", "callback_data": "m:menu"},
        ])
        return {"inline_keyboard": rows}

    def _pick_dish(self, chat_id: int, message_id: int | None, dish_id: int) -> None:
        dish = self.db.get_dish(dish_id)
        if not dish:
            self.api.send_message(chat_id, "Блюдо не найдено — возможно, удалено.")
            return
        text = (
            f"<b>{fmt.esc(dish['name'])}</b>\n"
            f"{fmt.num(dish['kcal'])} ккал на 100 г · Б {fmt.num(dish['protein'])} · "
            f"Ж {fmt.num(dish['fat'])} · У {fmt.num(dish['carb'])}\n\n"
            "Сколько грамм?"
        )
        grams = [50, 100, 150, 200, 250]
        buttons = [{"text": f"{g}", "callback_data": f"g:{dish_id}:{g}"} for g in grams[:3]]
        buttons2 = [{"text": f"{g}", "callback_data": f"g:{dish_id}:{g}"} for g in grams[3:]]
        kb = {
            "inline_keyboard": [
                buttons,
                buttons2,
                [{"text": "✏️ Свой вес", "callback_data": f"gw:{dish_id}"}],
                [{"text": "⬅️ Назад", "callback_data": "m:eat"}],
            ]
        }
        self._edit(chat_id, message_id, text, kb)

    def _log_dish(self, chat_id: int, message_id: int | None, dish_id: int, grams: float) -> None:
        dish = self.db.get_dish(dish_id)
        if not dish:
            self.api.send_message(chat_id, "Блюдо не найдено.")
            return
        factor = grams / 100.0
        self.db.log_meal(
            date=fmt.today(self.tz),
            time=fmt.now(self.tz).strftime("%H:%M"),
            name=dish["name"],
            kcal=round(dish["kcal"] * factor, 1),
            protein=round(dish["protein"] * factor, 1),
            fat=round(dish["fat"] * factor, 1),
            carb=round(dish["carb"] * factor, 1),
            grams=grams,
            dish_id=dish_id,
        )
        lines = [
            f"✅ <b>{fmt.esc(dish['name'])}</b> — {fmt.num(grams)} г",
            f"{fmt.kcal(dish['kcal'] * factor)} ккал · Б {fmt.num(dish['protein'] * factor)} · "
            f"Ж {fmt.num(dish['fat'] * factor)} · У {fmt.num(dish['carb'] * factor)}",
            "",
            self._totals_footer(),
        ]
        kb = {
            "inline_keyboard": [
                [
                    {"text": "↩️ Отменить", "callback_data": "u"},
                    {"text": "📊 Сегодня", "callback_data": "m:today"},
                ],
                [{"text": "➕ Ещё", "callback_data": "m:eat"}],
            ]
        }
        self._edit(chat_id, message_id, "\n".join(lines), kb)

    def _totals_footer(self) -> str:
        today = fmt.today(self.tz)
        totals = self.db.totals_for_date(today)
        goal = self.db.get_goal()
        if goal:
            left = goal["kcal"] - totals["kcal"]
            tail = f"осталось {fmt.kcal(left)}" if left >= 0 else f"⚠️ перебор {fmt.kcal(-left)}"
            return f"Сегодня: <b>{fmt.kcal(totals['kcal'])}</b> / {fmt.kcal(goal['kcal'])} ккал · {tail}"
        return f"Сегодня: <b>{fmt.kcal(totals['kcal'])}</b> ккал"

    # ------------------------------------------------------------ фото и ИИ

    @property
    def _ai_on(self) -> bool:
        return bool(self.ai and getattr(self.ai, "enabled", False))

    def _on_photo(self, chat_id: int, file_id: str, mime: str) -> None:
        if not self._ai_on:
            self.api.send_message(
                chat_id,
                "📷 Фото пока не разбираю — нейросеть выключена.\n\n"
                "Включить: секция <code>\"ai\"</code> в config.json (можно бесплатно, "
                "на локальной модели — см. README) и перезапустить бота.\n\n"
                "Пока запиши словами: /eat сырники 150",
                MAIN_MENU,
            )
            return
        self.api.send_message(chat_id, "🔍 Смотрю фото…")
        try:
            path = self.api.get_file(file_id)
            raw = self.api.download_file(path)
            items = self.ai.analyze_photo(raw, dishes=self.db.all_dishes(), mime=mime)
        except Exception as exc:  # AIError, TelegramError, сетевые сбои
            log.warning("Фото не разобрано: %s", exc)
            self.api.send_message(
                chat_id,
                f"⚠️ Не получилось разобрать фото: {fmt.esc(str(exc))}\n\n"
                "Попробуй ещё раз или запиши словами: /eat сырники 150",
                MAIN_MENU,
            )
            return
        if not items:
            self.api.send_message(
                chat_id,
                "🍽 На фото не вижу еды. Сними тарелку целиком и сверху — так точнее.",
                MAIN_MENU,
            )
            return
        self.states[chat_id] = {"step": "photo", "data": {"items": items}}
        self.api.send_message(chat_id, self._photo_draft_text(items), self._photo_draft_kb())

    def _photo_draft_text(self, items: list[dict]) -> str:
        lines = ["📷 <b>Что вижу на фото</b>", ""]
        for index, item in enumerate(items, 1):
            grams = item.get("grams")
            head = f"{index}. <b>{fmt.esc(item['name'])}</b>"
            if grams:
                head += f" — {fmt.num(grams)} г"
            lines.append(head)
            likely_local = self.db.find_dish_by_name(item["name"])
            tail = (f"   {fmt.kcal(item['kcal'])} ккал · Б {fmt.num(item['protein'])} · "
                    f"Ж {fmt.num(item['fat'])} · У {fmt.num(item['carb'])}")
            if likely_local:
                tail += " · есть в базе"
            lines.append(tail)
            note = (item.get("note") or "").strip()
            if note:
                lines.append(f"   <i>{fmt.esc(note)}</i>")
        total_kcal = sum(float(item.get("kcal") or 0) for item in items)
        lines += ["", f"Итого: <b>{fmt.kcal(total_kcal)}</b> ккал"]
        if self.webapp_url:
            lines.append("Точные граммы удобнее поправить в /app.")
        return "\n".join(lines)

    def _photo_draft_kb(self) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ Записать всё", "callback_data": "ai:save"},
                    {"text": "❌ Отмена", "callback_data": "ai:no"},
                ],
                [{"text": "📊 Сегодня", "callback_data": "m:today"}],
            ]
        }

    def _photo_action(self, chat_id: int, message_id: int | None, action: str) -> None:
        state = self.states.get(chat_id) or {}
        if state.get("step") != "photo":
            self._edit(chat_id, message_id, "Этот черновик уже неактуален — пришли фото заново.",
                       MAIN_MENU)
            return
        items = (state.get("data") or {}).get("items") or []
        if action == "no":
            self.states.pop(chat_id, None)
            self._edit(chat_id, message_id, "Ок, ничего не записал.", MAIN_MENU)
            return
        if action != "save":
            return
        self.states.pop(chat_id, None)
        today = fmt.today(self.tz)
        moment = fmt.now(self.tz).strftime("%H:%M")
        for item in items:
            local = self.db.find_dish_by_name(item["name"])
            grams = item.get("grams")
            if local and grams:
                factor = float(grams) / 100.0
                kcal = round(local["kcal"] * factor, 1)
                protein = round(local["protein"] * factor, 1)
                fat = round(local["fat"] * factor, 1)
                carb = round(local["carb"] * factor, 1)
                name, dish_id = local["name"], local["id"]
            else:
                kcal = float(item.get("kcal") or 0)
                protein = float(item.get("protein") or 0)
                fat = float(item.get("fat") or 0)
                carb = float(item.get("carb") or 0)
                name, dish_id = item["name"], None
            self.db.log_meal(
                date=today, time=moment, name=name, kcal=kcal, protein=protein,
                fat=fat, carb=carb, grams=grams, dish_id=dish_id,
            )
        lines = ["✅ <b>Записал с фото</b>", ""]
        for item in items:
            grams = item.get("grams")
            head = f"• {fmt.esc(item['name'])}"
            if grams:
                head += f" — {fmt.num(grams)} г"
            lines.append(f"{head} · {fmt.kcal(item['kcal'])} ккал")
        lines += ["", self._totals_footer()]
        kb = {
            "inline_keyboard": [
                [
                    {"text": "↩️ Отменить", "callback_data": "u"},
                    {"text": "📊 Сегодня", "callback_data": "m:today"},
                ],
            ]
        }
        self._edit(chat_id, message_id, "\n".join(lines), kb)

    def _ask_weight(self, chat_id: int, dish_id: int) -> None:
        dish = self.db.get_dish(dish_id)
        if not dish:
            return
        self.states[chat_id] = {"step": "weight", "data": {"dish_id": dish_id}}
        self.api.send_message(
            chat_id,
            f"Сколько грамм «{fmt.esc(dish['name'])}»? Напиши число, например 180.\n"
            "Потом можно изменить: /undo и заново.",
        )

    # ------------------------------------------------------------ блюда

    def _show_dishes(self, chat_id: int, page: int = 0, message_id: int | None = None) -> None:
        dishes = self.db.all_dishes()
        if not dishes:
            text = ("<b>🍱 Мои блюда</b>\n\nПока пусто. Добавь первое блюдо — "
                    "укажи КБЖУ на 100 г, дальше бот будет считать сам.")
            kb = {"inline_keyboard": [
                [{"text": "➕ Добавить блюдо", "callback_data": "add"}],
                [{"text": "⬅️ В меню", "callback_data": "m:menu"}],
            ]}
            if message_id:
                self._edit(chat_id, message_id, text, kb)
            else:
                self.api.send_message(chat_id, text, kb)
            return

        per_page = 6
        pages = max(1, (len(dishes) + per_page - 1) // per_page)
        page = max(0, min(page, pages - 1))
        chunk = dishes[page * per_page:(page + 1) * per_page]

        lines = [f"<b>🍱 Мои блюда</b> · всего {len(dishes)}"]
        if pages > 1:
            lines[0] += f" · стр. {page + 1}/{pages}"
        lines.append("")
        rows: list[list[dict]] = []
        for dish in chunk:
            lines.append(f"• {fmt.esc(dish['name'])} — {fmt.num(dish['kcal'])} ккал/100 г")
            rows.append([{
                "text": f"{_cut(dish['name'])} · {fmt.num(dish['kcal'])}",
                "callback_data": f"d:{dish['id']}",
            }])
        lines += ["", "Нажми на блюдо, чтобы записать его в дневник."]

        nav: list[dict] = []
        if pages > 1:
            if page > 0:
                nav.append({"text": "◀️", "callback_data": f"dp:{page - 1}"})
            if page < pages - 1:
                nav.append({"text": "▶️", "callback_data": f"dp:{page + 1}"})
        if nav:
            rows.append(nav)
        rows.append([
            {"text": "🗑 Удалить", "callback_data": "del"},
            {"text": "➕ Новое", "callback_data": "add"},
        ])
        rows.append([{"text": "⬅️ В меню", "callback_data": "m:menu"}])

        text = "\n".join(lines)
        kb = {"inline_keyboard": rows}
        if message_id:
            self._edit(chat_id, message_id, text, kb)
        else:
            self.api.send_message(chat_id, text, kb)

    def _show_delete_list(self, chat_id: int, message_id: int | None) -> None:
        dishes = self.db.all_dishes()
        if not dishes:
            self._show_dishes(chat_id, 0, message_id)
            return
        rows = [[{"text": f"❌ {_cut(d['name'], 28)}", "callback_data": f"dd:{d['id']}"}] for d in dishes]
        rows.append([{"text": "⬅️ Назад", "callback_data": "m:dishes"}])
        text = ("<b>🗑 Удаление блюда</b>\n\n"
                "Записи в дневнике останутся — в них хранятся уже посчитанные цифры.\n"
                "Выбери блюдо:")
        kb = {"inline_keyboard": rows}
        if message_id:
            self._edit(chat_id, message_id, text, kb)
        else:
            self.api.send_message(chat_id, text, kb)

    def _delete_dish(self, chat_id: int, dish_id: int, message_id: int | None) -> None:
        dish = self.db.get_dish(dish_id)
        self.db.delete_dish(dish_id)
        if dish:
            self.api.send_message(chat_id, f"🗑 Удалено: {fmt.esc(dish['name'])}")
        self._show_delete_list(chat_id, message_id)

    # ------------------------------------------------------------ тренировка

    def _workout_text(self) -> str:
        today = fmt.today(self.tz)
        day = self.db.get_day(today)
        mark = "была" if day and day["workout"] else "не отмечена"
        lines = [f"<b>🏋️ Тренировка, {fmt.short_date(today)}</b>", "", f"Сейчас: <b>{mark}</b>"]
        if day and day["note"]:
            lines.append(f"Заметка: {fmt.esc(day['note'])}")
        lines += ["", "Отметь, чтобы в статистике было видно дни тренировок."]
        return "\n".join(lines)

    def _workout_kb(self) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ Была", "callback_data": "w:y"},
                    {"text": "❌ Не было", "callback_data": "w:n"},
                ],
                [
                    {"text": "✏️ Заметка", "callback_data": "w:note"},
                    {"text": "⬅️ В меню", "callback_data": "m:menu"},
                ],
            ]
        }

    def _workout_action(self, chat_id: int, message_id: int | None, action: str) -> None:
        today = fmt.today(self.tz)
        if action == "y":
            self.db.set_workout(today, True)
            self._edit(chat_id, message_id, self._workout_text(), self._workout_kb())
        elif action == "n":
            self.db.set_workout(today, False)
            self._edit(chat_id, message_id, self._workout_text(), self._workout_kb())
        elif action == "note":
            self.states[chat_id] = {"step": "workout_note", "data": {}}
            self.api.send_message(chat_id, "Что за тренировка? Например: «ноги, 45 мин». "
                                           "Отправь текст или /cancel.")

    def _cmd_workout(self, chat_id: int, args: str) -> None:
        args = args.strip().lower()
        today = fmt.today(self.tz)
        if args in ("yes", "y", "1", "+", "да"):
            self.db.set_workout(today, True)
            self.api.send_message(chat_id, "🏋️ Отметил: тренировка была.", self._workout_kb())
        elif args in ("no", "n", "0", "-", "нет"):
            self.db.set_workout(today, False)
            self.api.send_message(chat_id, "Отметил: тренировки не было.", self._workout_kb())
        elif args:
            self.db.set_workout(today, True, args)
            self.api.send_message(chat_id, f"🏋️ Записал тренировку: {fmt.esc(args)}", self._workout_kb())
        else:
            self.api.send_message(chat_id, self._workout_text(), self._workout_kb())

    # ------------------------------------------------------------ статистика

    def _show_stats(self, chat_id: int, period: int, message_id: int | None = None) -> None:
        text = self._stats_text(period)
        kb = {
            "inline_keyboard": [
                [
                    {"text": "7 дней", "callback_data": "s:7"},
                    {"text": "30 дней", "callback_data": "s:30"},
                    {"text": "Всё время", "callback_data": "s:0"},
                ],
                [{"text": "⬅️ В меню", "callback_data": "m:menu"}],
            ]
        }
        if message_id:
            self._edit(chat_id, message_id, text, kb)
        else:
            self.api.send_message(chat_id, text, kb)

    def _stats_text(self, period: int) -> str:
        today_str = fmt.today(self.tz)
        today = fmt.parse_date(today_str)
        if period:
            start_str = (today - timedelta(days=period - 1)).isoformat()
            title = f"за {period} дн."
        else:
            start_str = self.db.first_meal_date() or today_str
            title = "за всё время"

        meals = self.db.meals_between(start_str, today_str)
        days_map = self.db.days_between(start_str, today_str)
        goal = self.db.get_goal()

        by_day: dict[str, dict[str, float]] = defaultdict(
            lambda: {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carb": 0.0, "n": 0}
        )
        for meal in meals:
            bucket = by_day[meal["date"]]
            bucket["kcal"] += meal["kcal"]
            bucket["protein"] += meal["protein"]
            bucket["fat"] += meal["fat"]
            bucket["carb"] += meal["carb"]
            bucket["n"] += 1

        span = (today - fmt.parse_date(start_str)).days + 1
        filled_days = len(by_day)
        if not filled_days:
            return (f"<b>📈 Статистика {title}</b>\n\n"
                    "Записей нет. Добавь первый приём пищи — и здесь появятся цифры.")

        avg = {key: sum(b[key] for b in by_day.values()) / filled_days
               for key in ("kcal", "protein", "fat", "carb")}

        all_workouts = [d for d, value in days_map.items() if value["workout"]]
        workout_days = [d for d in all_workouts if d in by_day]
        rest_days = [d for d in by_day if d not in all_workouts]

        lines = [f"<b>📈 Статистика {title}</b>",
                 f"{fmt.short_date(start_str)} – {fmt.short_date(today_str)}", ""]
        lines.append(f"Дней с записями: <b>{filled_days}</b> из {span}")
        lines.append(f"В среднем за день: <b>{fmt.kcal(avg['kcal'])}</b> ккал · "
                     f"Б {fmt.num(avg['protein'])} · Ж {fmt.num(avg['fat'])} · У {fmt.num(avg['carb'])}")

        if goal and goal["kcal"]:
            in_range = sum(
                1 for b in by_day.values() if abs(b["kcal"] - goal["kcal"]) <= goal["kcal"] * 0.1
            )
            lines.append(f"Цель {fmt.kcal(goal['kcal'])} ккал: в коридоре ±10% — <b>{in_range}</b> "
                         f"из {filled_days} дней")

        lines.append("")
        if all_workouts:
            lines.append(f"🏋️ Тренировок отмечено: <b>{len(all_workouts)}</b>")
        else:
            lines.append("🏋️ Тренировки не отмечены")
        if workout_days:
            w_kcal = sum(by_day[d]["kcal"] for d in workout_days) / len(workout_days)
            w_prot = sum(by_day[d]["protein"] for d in workout_days) / len(workout_days)
            lines.append(f"  в дни тренировок: {fmt.kcal(w_kcal)} ккал · Б {fmt.num(w_prot)}")
            if rest_days:
                r_kcal = sum(by_day[d]["kcal"] for d in rest_days) / len(rest_days)
                r_prot = sum(by_day[d]["protein"] for d in rest_days) / len(rest_days)
                diff = w_kcal - r_kcal
                sign = "+" if diff >= 0 else "−"
                lines.append(f"  в дни отдыха: {fmt.kcal(r_kcal)} ккал · Б {fmt.num(r_prot)}")
                lines.append(f"  разница: <b>{sign}{fmt.kcal(abs(diff))}</b> ккал")
        elif all_workouts:
            lines.append("  но в эти дни еда не записана — сравнивать нечего")

        lines.append("")
        if period and period <= 7:
            lines.append("<b>По дням</b>")
            for offset in range(period):
                day = (fmt.parse_date(start_str) + timedelta(days=offset)).isoformat()
                bucket = by_day.get(day)
                mark = "🏋️" if days_map.get(day) and days_map[day]["workout"] else "  "
                if not bucket:
                    lines.append(f"{fmt.short_date(day)} {fmt.WEEKDAYS[fmt.parse_date(day).weekday()]} "
                                 f"{mark} —")
                    continue
                value = bucket["kcal"]
                if goal and goal["kcal"]:
                    tail = fmt.bar(value, goal["kcal"], 8)
                    delta = value - goal["kcal"]
                    if delta > 0:
                        tail += f" +{fmt.kcal(delta)}"
                else:
                    tail = f"{fmt.kcal(value)} ккал"
                lines.append(f"{fmt.short_date(day)} {fmt.WEEKDAYS[fmt.parse_date(day).weekday()]} "
                             f"{mark} <b>{fmt.kcal(value)}</b> {tail}")
        else:
            lines.append("<b>По неделям</b>")
            for label, week_days, wk_start, wk_end in self._weeks(start_str, today_str, by_day, days_map):
                if not week_days:
                    lines.append(f"{label}: —")
                    continue
                kcal = sum(by_day[d]["kcal"] for d in week_days) / len(week_days)
                wk = sum(1 for d in all_workouts if wk_start <= d <= wk_end)
                lines.append(f"{label}: {fmt.kcal(kcal)} ккал/день · 🏋️ {wk} · "
                             f"дней с записями {len(week_days)}")

        return "\n".join(lines)

    def _weeks(self, start_str: str, end_str: str, by_day: dict, days_map: dict):
        """Группирует дни периода по неделям с понедельника."""
        start = fmt.parse_date(start_str)
        end = fmt.parse_date(end_str)
        cursor = start - timedelta(days=start.weekday())
        while cursor <= end:
            week_end = cursor + timedelta(days=6)
            days = [d for d in by_day if cursor.isoformat() <= d <= week_end.isoformat()]
            label = f"{fmt.short_date(cursor.isoformat())}–{fmt.short_date(week_end.isoformat())}"
            yield label, sorted(days), cursor.isoformat(), week_end.isoformat()
            cursor += timedelta(days=7)

    # ------------------------------------------------------------ цели

    def _goal_text(self) -> str:
        goal = self.db.get_goal()
        if not goal:
            return ("<b>⚙️ Цели</b>\n\nПока не заданы.\n\n"
                    "Отправь четыре числа: ккал, белки, жиры, углеводы.\n"
                    "Например: <code>2200 150 70 220</code>")
        return (f"<b>⚙️ Цели</b>\n\n"
                f"Ккал: <b>{fmt.num(goal['kcal'])}</b>\n"
                f"Белки: <b>{fmt.num(goal['protein'])}</b>\n"
                f"Жиры: <b>{fmt.num(goal['fat'])}</b>\n"
                f"Углеводы: <b>{fmt.num(goal['carb'])}</b>")

    def _goal_kb(self) -> dict:
        return {"inline_keyboard": [
            [{"text": "✏️ Изменить", "callback_data": "gz"}],
            [{"text": "⬅️ В меню", "callback_data": "m:menu"}],
        ]}

    def _start_goal(self, chat_id: int) -> None:
        self.states[chat_id] = {"step": "goal", "data": {}}
        self.api.send_message(
            chat_id,
            "Отправь четыре числа: <b>ккал белки жиры углеводы</b>.\n"
            "Например: <code>2200 150 70 220</code>",
        )

    def _cmd_goal(self, chat_id: int, args: str) -> None:
        values = self._parse_numbers(args, 4)
        if values:
            self.db.set_goal(*values)
            self.api.send_message(chat_id, "✅ Цель сохранена.\n\n" + self._goal_text(), self._goal_kb())
        elif args:
            self.api.send_message(chat_id, "Нужно четыре числа: /goal 2200 150 70 220")
        else:
            msg = self._goal_text()
            if self.db.get_goal():
                self.api.send_message(chat_id, msg + "\n\nИзменить: /goal 2200 150 70 220", self._goal_kb())
            else:
                self.api.send_message(chat_id, msg + "\n\nЧерез /goal: /goal 2200 150 70 220", self._goal_kb())

    # ------------------------------------------------------------ поиск

    def _cmd_find(self, chat_id: int, args: str) -> None:
        if not args:
            self._ask_search(chat_id)
            return
        results = self.db.search_dishes(args)
        if not results:
            self.api.send_message(
                chat_id,
                f"По запросу «{fmt.esc(args)}» ничего нет.",
                {"inline_keyboard": [[{"text": "➕ Добавить", "callback_data": "add"}]]},
            )
            return
        self.api.send_message(chat_id, self._results_text("Нашёл:", results),
                              {"inline_keyboard": self._results_rows(results)})

    def _ask_search(self, chat_id: int) -> None:
        self.states[chat_id] = {"step": "search", "data": {}}
        self.api.send_message(chat_id, "Что искать? Напиши часть названия блюда.")

    # ------------------------------------------------------------ добавление блюда

    def _cmd_add(self, chat_id: int, args: str) -> None:
        if args:
            tokens = args.split()
            nums = tokens[-4:]
            values = self._parse_numbers(" ".join(nums), 4) if len(nums) == 4 else None
            if values:
                name = " ".join(tokens[:-4]).strip()
                if name:
                    self._save_dish(chat_id, name, *values)
                    return
        self.states[chat_id] = {"step": "add_name", "data": {"name": args} if args else {}}
        if args:
            self._ask_kcal(chat_id)
        else:
            self.api.send_message(chat_id, "Как назвать блюдо? Напиши название.")

    def _start_add(self, chat_id: int) -> None:
        self.states[chat_id] = {"step": "add_name", "data": {}}
        self.api.send_message(chat_id, "Как назвать блюдо? Напиши название.")

    def _ask_kcal(self, chat_id: int) -> None:
        self.states[chat_id]["step"] = "add_kcal"
        self.api.send_message(
            chat_id,
            "Сколько ккал на 100 г?\n\n"
            "Можно сразу всё: <code>220 18 9 24</code> — ккал, белки, жиры, углеводы.",
        )

    def _save_dish(self, chat_id: int, name: str, kcal: float, protein: float, fat: float, carb: float) -> None:
        dish_id, created = self.db.add_or_update_dish(name, kcal, protein, fat, carb)
        self.states.pop(chat_id, None)
        verb = "создал" if created else "обновил"
        text = (f"✅ {verb.capitalize()} <b>{fmt.esc(name)}</b>: {fmt.num(kcal)} ккал/100 г\n"
                f"Б {fmt.num(protein)} · Ж {fmt.num(fat)} · У {fmt.num(carb)}")
        kb = {"inline_keyboard": [
            [{"text": "🍽 Записать сейчас", "callback_data": f"d:{dish_id}"}],
            [{"text": "➕ Ещё блюдо", "callback_data": "add"},
             {"text": "⬅️ В меню", "callback_data": "m:menu"}],
        ]}
        self.api.send_message(chat_id, text, kb)

    # ------------------------------------------------------------ разовая запись

    def _start_quick(self, chat_id: int) -> None:
        self.states[chat_id] = {"step": "quick_name", "data": {}}
        self.api.send_message(chat_id, "Что съел? Напиши название.")

    def _ask_quick_kcal(self, chat_id: int) -> None:
        self.states[chat_id]["step"] = "quick_kcal"
        self.api.send_message(
            chat_id,
            "Сколько всего ккал?\n\n"
            "Можно добавить БЖУ: <code>320 25 12 30</code>.",
        )

    # ------------------------------------------------------------ отмена

    def _undo(self, chat_id: int, message_id: int | None = None) -> None:
        today = fmt.today(self.tz)
        meal = self.db.last_meal(today)
        if not meal:
            self.api.send_message(chat_id, "Сегодня пока нечего отменять.")
            return
        self.db.delete_meal(meal["id"])
        text = (f"↩️ Удалено: {fmt.esc(meal['name'])}"
                f"{' — ' + fmt.num(meal['grams']) + ' г' if meal['grams'] else ''} · "
                f"{fmt.kcal(meal['kcal'])} ккал\n\n" + self._totals_footer())
        kb = {"inline_keyboard": [
            [{"text": "📊 Сегодня", "callback_data": "m:today"},
             {"text": "➕ Добавить", "callback_data": "m:eat"}],
        ]}
        if message_id:
            self._edit(chat_id, message_id, text, kb)
        else:
            self.api.send_message(chat_id, text, kb)

    # ------------------------------------------------------------ экспорт

    def _export(self, chat_id: int) -> None:
        meals = self.db.all_meals()
        if not meals:
            self.api.send_message(chat_id, "Пока нечего выгружать.")
            return
        os.makedirs(self.export_dir, exist_ok=True)
        filename = f"kkal-{fmt.today(self.tz)}.csv"
        path = os.path.join(self.export_dir, filename)
        days_map = self.db.days_between("0000-01-01", "9999-12-31")
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(["дата", "время", "блюдо", "граммы", "ккал", "белки", "жиры",
                             "углеводы", "тренировка", "заметка"])
            for meal in meals:
                day = days_map.get(meal["date"])
                writer.writerow([
                    meal["date"], meal["time"], meal["name"],
                    fmt.num(meal["grams"]) if meal["grams"] else "",
                    fmt.num(meal["kcal"]), fmt.num(meal["protein"]),
                    fmt.num(meal["fat"]), fmt.num(meal["carb"]),
                    "да" if day and day["workout"] else "",
                    day["note"] if day and day["note"] else "",
                ])
        self.api.send_document(chat_id, path, caption=f"Выгрузка КБЖУ · {len(meals)} записей")

    # ------------------------------------------------------------ диалоги ввода

    def _on_state_text(self, chat_id: int, state: dict, text: str) -> None:
        step = state["step"]
        data = state["data"]

        if step == "add_name":
            if len(text) > 60:
                self.api.send_message(chat_id, "Слишком длинно, до 60 символов. Ещё раз?")
                return
            data["name"] = " ".join(text.split())
            self._ask_kcal(chat_id)

        elif step == "add_kcal":
            values = self._parse_numbers(text, 4)
            if values:
                self._save_dish(chat_id, data["name"], *values)
                return
            kcal = fmt.parse_number(text)
            if kcal is None or kcal < 0 or kcal > 1000:
                self.api.send_message(chat_id, "Нужно число от 0 до 1000 (ккал на 100 г).")
                return
            data["kcal"] = kcal
            state["step"] = "add_protein"
            self.api.send_message(chat_id, "Белки на 100 г? Если не знаешь — отправь «-».")

        elif step in ("add_protein", "add_fat", "add_carb"):
            key = {"add_protein": "protein", "add_fat": "fat", "add_carb": "carb"}[step]
            if text.strip() in ("-", "0", "нет", "skip", "пропустить"):
                data[key] = 0.0
            else:
                value = fmt.parse_number(text)
                if value is None or value < 0 or value > 100:
                    self.api.send_message(chat_id, "Нужно число от 0 до 100. Или «-».")
                    return
                data[key] = value

            order = [("add_protein", "add_fat", "Жиры на 100 г? Или «-»."),
                     ("add_fat", "add_carb", "Углеводы на 100 г? Или «-».")]
            nxt = dict((a, (b, msg)) for a, b, msg in order).get(step)
            if nxt:
                state["step"] = nxt[0]
                self.api.send_message(chat_id, nxt[1])
            else:
                self._save_dish(chat_id, data["name"], data["kcal"],
                                data.get("protein", 0), data.get("fat", 0), data["carb"])

        elif step == "weight":
            grams = fmt.parse_number(text)
            if grams is None or grams <= 0 or grams > 5000:
                self.api.send_message(chat_id, "Нужно число грамм, например 180.")
                return
            self.states.pop(chat_id, None)
            self._log_dish(chat_id, None, data["dish_id"], grams)

        elif step == "search":
            self.states.pop(chat_id, None)
            self._cmd_find(chat_id, text)

        elif step == "quick_name":
            data["name"] = " ".join(text.split())
            self._ask_quick_kcal(chat_id)

        elif step == "quick_kcal":
            values = self._parse_numbers(text, 4)
            if values:
                self._save_quick(chat_id, data["name"], *values)
                return
            kcal = fmt.parse_number(text)
            if kcal is None or kcal <= 0 or kcal > 5000:
                self.api.send_message(chat_id, "Нужно число ккал, например 320.")
                return
            self._save_quick(chat_id, data["name"], kcal, 0, 0, 0)

        elif step == "goal":
            values = self._parse_numbers(text, 4)
            if not values:
                self.api.send_message(chat_id, "Нужно четыре числа: 2200 150 70 220")
                return
            self.states.pop(chat_id, None)
            self.db.set_goal(*values)
            self.api.send_message(chat_id, "✅ Цель сохранена.\n\n" + self._goal_text(), self._goal_kb())

        elif step == "workout_note":
            self.states.pop(chat_id, None)
            self.db.set_workout(fmt.today(self.tz), True, text.strip()[:200])
            self.api.send_message(chat_id, "🏋️ Записал.", self._workout_kb())

        else:
            self.states.pop(chat_id, None)
            self.api.send_message(chat_id, "Сбросил диалог. /help", MAIN_MENU)

    def _save_quick(self, chat_id: int, name: str, kcal: float,
                    protein: float, fat: float, carb: float) -> None:
        self.states.pop(chat_id, None)
        self.db.log_meal(
            date=fmt.today(self.tz),
            time=fmt.now(self.tz).strftime("%H:%M"),
            name=name,
            kcal=round(kcal, 1),
            protein=round(protein, 1),
            fat=round(fat, 1),
            carb=round(carb, 1),
        )
        text = (f"✅ <b>{fmt.esc(name)}</b> — {fmt.kcal(kcal)} ккал\n"
                f"Б {fmt.num(protein)} · Ж {fmt.num(fat)} · У {fmt.num(carb)}\n\n"
                + self._totals_footer())
        kb = {"inline_keyboard": [
            [{"text": "↩️ Отменить", "callback_data": "u"},
             {"text": "📊 Сегодня", "callback_data": "m:today"}],
        ]}
        self.api.send_message(chat_id, text, kb)

    # ------------------------------------------------------------ утилиты

    @staticmethod
    def _parse_numbers(text: str, count: int) -> list[float] | None:
        parts = text.replace(",", ".").split()
        if len(parts) != count:
            return None
        values = [fmt.parse_number(part) for part in parts]
        if any(value is None for value in values):
            return None
        return values  # type: ignore[return-value]

    def _edit(self, chat_id: int, message_id: int | None, text: str, kb: dict | None = None) -> None:
        if message_id:
            self.api.edit_message(chat_id, message_id, text, kb)
        else:
            self.api.send_message(chat_id, text, kb)

    def _cmd_eat(self, chat_id: int, args: str) -> None:
        """'/eat сырники 150' — быстрая запись по названию."""
        if not args:
            self.states.pop(chat_id, None)
            self.api.send_message(chat_id, self._pick_text(), self._pick_kb())
            return

        tokens = args.split()
        grams = None
        if len(tokens) > 1:
            maybe = fmt.parse_number(tokens[-1])
            if maybe and maybe > 0:
                grams = maybe
                tokens = tokens[:-1]
        query = " ".join(tokens)

        results = self.db.search_dishes(query)
        if not results:
            self.api.send_message(
                chat_id,
                f"«{fmt.esc(query)}» не нашёл в базе.",
                {"inline_keyboard": [
                    [{"text": "➕ Добавить блюдо", "callback_data": "add"}],
                    [{"text": "⚡ Записать разово", "callback_data": "q"}],
                ]},
            )
            return
        if grams:
            self._log_dish(chat_id, None, results[0]["id"], grams)
        elif len(results) == 1:
            self._pick_dish(chat_id, None, results[0]["id"])
        else:
            self.api.send_message(chat_id, self._results_text("Уточни блюдо:", results),
                                  {"inline_keyboard": self._results_rows(results)})

    @staticmethod
    def _results_text(title: str, results: list) -> str:
        lines = [f"<b>{title}</b>", ""]
        for dish in results[:8]:
            lines.append(f"• {fmt.esc(dish['name'])} — {fmt.num(dish['kcal'])} ккал/100 г")
        lines += ["", "Нажми на блюдо ниже, чтобы записать."]
        return "\n".join(lines)

    @staticmethod
    def _results_rows(results: list) -> list[list[dict]]:
        return [[{"text": f"{_cut(d['name'])} · {fmt.num(d['kcal'])}", "callback_data": f"d:{d['id']}"}]
                for d in results[:8]]
