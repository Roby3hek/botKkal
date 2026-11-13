"""JSON API мини-приложения: тонкая обёртка над DB + ИИ.

Все ответы — словари, сериализуются в server.py. Мутации возвращают свежий
экран «день», чтобы фронтенд перерисовывался одним присваиванием.
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import timedelta

from . import fmt
from .ai import AI, AIError
from .auth import AuthError, parse_init_data
from .db import DB

MAX_BODY = 8 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ------------------------------------------------------------------ утилиты


def _date(value: str | None, default: str) -> str:
    text = (value or "").strip() or default
    try:
        fmt.parse_date(text)
    except ValueError:
        raise ApiError("дата должна быть в формате ГГГГ-ММ-ДД")
    return text


def _period(value) -> int:
    try:
        period = int(value)
    except (TypeError, ValueError):
        return 7
    return period if period in (0, 7, 30, 90) else 7


def _float(value, name: str, default: float = 0.0, maximum: float = 100_000.0) -> float:
    if value is None or value == "":
        return default
    try:
        number = float(str(value).replace(",", "."))
    except ValueError:
        raise ApiError(f"{name}: нужно число")
    if number != number:  # NaN
        raise ApiError(f"{name}: нужно число")
    if number < 0:
        raise ApiError(f"{name}: не может быть отрицательным")
    return min(number, maximum)


def _int_or_none(value) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError("кривой id")


def _dish_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "kcal": round(float(row["kcal"]), 1),
        "protein": round(float(row["protein"]), 1),
        "fat": round(float(row["fat"]), 1),
        "carb": round(float(row["carb"]), 1),
        "use_count": row["use_count"],
        "last_used": row["last_used"],
    }


def _meal_dict(row) -> dict:
    return {
        "id": row["id"],
        "date": row["date"],
        "time": row["time"],
        "name": row["name"],
        "dish_id": row["dish_id"],
        "grams": row["grams"],
        "kcal": round(float(row["kcal"]), 1),
        "protein": round(float(row["protein"]), 1),
        "fat": round(float(row["fat"]), 1),
        "carb": round(float(row["carb"]), 1),
    }


def _daterange(start: str, end: str) -> list[str]:
    first, last = fmt.parse_date(start), fmt.parse_date(end)
    days, cursor = [], first
    while cursor <= last:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


# ------------------------------------------------------------------ API


class WebAPI:
    def __init__(self, db_path: str, token: str, config: dict | None = None,
                 demo: bool = False, ai: AI | None = None):
        self.db_path = db_path
        self.token = token
        self.config = config or {}
        self.demo = demo
        cfg = self.config
        self.tz_offset = float(cfg.get("utc_offset", 4))
        try:
            from .handlers import _norm_user  # noqa: F401  (единая нормализация ников)
        except Exception:  # pragma: no cover
            _norm_user = None
        owner = cfg.get("owner_id")
        self.owner_id = 0
        self.owner_username = ""
        raw = str(owner if owner is not None else "").strip()
        if raw and raw not in ("0", "None"):
            try:
                self.owner_id = int(raw)
            except ValueError:
                self.owner_username = raw.lstrip("@").lower()
        if ai is not None:
            self.ai = ai
        else:
            try:
                self.ai = AI(cfg.get("ai") or {})
            except Exception:  # кривая секция ai не должна ронять приложение
                self.ai = AI({})

    # -------------------------------------------------------------- сессия

    def _session(self, init_data: str) -> dict:
        if init_data:
            try:
                return parse_init_data(init_data, self.token)
            except AuthError as exc:
                # Демо-режим не смягчает проверку подписи: если initData пришёл,
                # он должен быть настоящим.
                raise ApiError(str(exc), 401)
        if self.demo:
            return {"user": {"id": 0, "first_name": "Демо", "username": "demo"}, "auth_date": 0}
        raise ApiError("нужен initData от Telegram", 401)

    def _owner_ok(self, user: dict) -> bool:
        if not self.owner_id and not self.owner_username:
            return True
        if self.owner_id and int(user.get("id") or 0) == self.owner_id:
            return True
        username = str(user.get("username") or "").strip().lstrip("@").lower()
        return bool(self.owner_username and username == self.owner_username)

    # -------------------------------------------------------------- база

    def _db(self) -> DB:
        return DB(self.db_path)

    def _now(self):
        return fmt.now(self.tz_offset)

    def _today(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    # -------------------------------------------------------------- день

    def _day_payload(self, db: DB, date: str) -> dict:
        today = self._today()
        meals = db.meals_for_date(date)
        totals = db.totals_for_date(date)
        goal = db.get_goal()
        row = db.get_day(date)
        return {
            "date": date,
            "label": fmt.human_date(date, today),
            "short": fmt.short_date(date),
            "is_today": date == today,
            "workout": bool(row["workout"]) if row else False,
            "note": (row["note"] if row else "") or "",
            "meals": [_meal_dict(row) for row in meals],
            "totals": {k: round(float(v), 1) for k, v in totals.items()},
            "goal": goal,
            "ai": self.ai.status(),
        }

    # -------------------------------------------------------------- статистика

    def _stats(self, db: DB, period: int) -> dict:
        today = self._today()
        if period:
            start = (fmt.parse_date(today) - timedelta(days=period - 1)).isoformat()
        else:
            start = db.first_meal_date() or today
        dates = _daterange(start, today)
        marks = db.days_between(start, today)
        meals = db.meals_between(start, today)

        done: dict[str, dict[str, float]] = {}
        for meal in meals:
            bucket = done.setdefault(meal["date"], {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carb": 0.0})
            for key in bucket:
                bucket[key] += float(meal[key])

        goal = db.get_goal()
        by_day = []
        for date in dates:
            values = done.get(date)
            mark = marks.get(date)
            by_day.append({
                "date": date,
                "short": fmt.short_date(date),
                "weekday": fmt.WEEKDAYS[fmt.parse_date(date).weekday()],
                "kcal": round(values["kcal"], 1) if values else 0,
                "filled": bool(values),
                "workout": bool(mark["workout"]) if mark else False,
                "goal": goal["kcal"] if goal else None,
            })

        active = [entry for entry in by_day if entry["filled"]]
        count = len(active) or 1
        totals = {key: round(sum(float(m[key]) for m in meals), 1) for key in ("kcal", "protein", "fat", "carb")}
        summary = {
            "period": period,
            "start": start,
            "end": today,
            "days": len(dates),
            "days_filled": len(active),
            "workouts": sum(1 for entry in by_day if entry["workout"]),
            "by_day": by_day,
            "totals": totals,
            "avg": {key: round(totals[key] / count, 1) for key in totals},
            "goal": goal,
        }
        summary["weeks"] = self._weeks(by_day, goal)

        first = db.first_meal_date()
        summary["first_date"] = first
        return summary

    def _weeks(self, by_day: list[dict], goal: dict | None) -> list[dict]:
        """Недельные отрезки по 7 дней, свежие — последними."""
        if not by_day:
            return []
        chunks = []
        tail = by_day[-7:]
        chunks.append(tail)
        rest = by_day[:-7]
        while rest and len(chunks) < 5:
            chunks.append(rest[-7:])
            rest = rest[:-7]
        chunks.reverse()

        weeks = []
        for chunk in chunks:
            filled = [entry for entry in chunk if entry["filled"]]
            kcal = sum(entry["kcal"] for entry in filled)
            average = round(kcal / len(filled)) if filled else 0
            weeks.append({
                "label": f"{chunk[0]['short']}–{chunk[-1]['short']}",
                "avg_kcal": average,
                "days": len(filled),
                "workouts": sum(1 for entry in chunk if entry["workout"]),
                "goal": round(goal["kcal"]) if goal else None,
            })
        return weeks

    # -------------------------------------------------------------- маршруты

    def handle(self, method: str, path: str, query: dict, body: dict | str | None,
               headers: dict) -> tuple[int, object, str]:
        try:
            return self._dispatch(method.upper(), path, query, body, headers)
        except ApiError as exc:
            return exc.status, {"ok": False, "error": exc.message}, "application/json; charset=utf-8"
        except AIError as exc:
            return 502, {"ok": False, "error": f"ИИ: {exc}"}, "application/json; charset=utf-8"

    def _dispatch(self, method: str, path: str, query: dict, body, headers) -> tuple[int, object, str]:
        json_type = "application/json; charset=utf-8"
        if path == "/api/health":
            return 200, {"ok": True, "app": "kkal", "ai": self.ai.status()}, json_type

        header_data = (headers.get("x-init-data") or headers.get("x-telegram-init-data")
                       or headers.get("X-Init-Data") or "")
        init_data = header_data
        if not init_data and isinstance(body, dict):
            init_data = str(body.get("initData") or "")
        session = self._session(init_data)
        user = session["user"]
        if not self._owner_ok(user):
            raise ApiError("🔒 Это личный бот одного человека", 403)

        demo = not bool(init_data)

        if path == "/api/session":
            db = self._db()
            try:
                payload = {
                    "ok": True,
                    "user": {"id": user.get("id"), "username": user.get("username") or "",
                             "first_name": user.get("first_name") or ""},
                    "today": self._today(),
                    "tz_offset": self.tz_offset,
                    "demo": demo,
                    "ai": self.ai.status(),
                    "day": self._day_payload(db, self._today()),
                }
            finally:
                db.close()
            return 200, payload, json_type

        if path == "/api/day" and method == "GET":
            date = _date(query.get("date"), self._today())
            db = self._db()
            try:
                return 200, {"ok": True, "day": self._day_payload(db, date),
                             "prev": (fmt.parse_date(date) - timedelta(days=1)).isoformat(),
                             "next": (fmt.parse_date(date) + timedelta(days=1)).isoformat()}, json_type
            finally:
                db.close()

        if path == "/api/meals" and method == "POST":
            return 200, self._add_meal(body if isinstance(body, dict) else {}), json_type

        match = re.fullmatch(r"/api/meals/(\d+)", path)
        if match and method == "DELETE":
            meal_id = int(match.group(1))
            db = self._db()
            try:
                meal = next((m for m in db.all_meals() if m["id"] == meal_id), None)
                if not meal:
                    raise ApiError("запись не найдена", 404)
                date = meal["date"]
                db.delete_meal(meal_id)
                return 200, {"ok": True, "day": self._day_payload(db, date)}, json_type
            finally:
                db.close()

        if path == "/api/dishes" and method == "GET":
            query_text = str(query.get("q") or "").strip()
            db = self._db()
            try:
                if query_text:
                    rows = db.search_dishes(query_text)
                else:
                    rows = db.popular_dishes(40)
                items = [_dish_dict(row) for row in rows][:60]
                return 200, {"ok": True, "items": items, "query": query_text,
                             "total": len(items)}, json_type
            finally:
                db.close()

        if path == "/api/dishes" and method == "POST":
            data = body if isinstance(body, dict) else {}
            name = " ".join(str(data.get("name") or "").split())
            if not name:
                raise ApiError("название блюда обязательно")
            if len(name) > 120:
                raise ApiError("название длиннее 120 символов")
            kcal = _float(data.get("kcal"), "ккал", maximum=10_000)
            protein = _float(data.get("protein"), "белки", maximum=1000)
            fat = _float(data.get("fat"), "жиры", maximum=1000)
            carb = _float(data.get("carb"), "углеводы", maximum=1000)
            db = self._db()
            try:
                dish_id, created = db.add_or_update_dish(name, kcal, protein, fat, carb)
                row = db.get_dish(dish_id)
                return 200, {"ok": True, "dish": _dish_dict(row), "created": created}, json_type
            finally:
                db.close()

        match = re.fullmatch(r"/api/dishes/(\d+)", path)
        if match and method == "DELETE":
            dish_id = int(match.group(1))
            db = self._db()
            try:
                if not db.get_dish(dish_id):
                    raise ApiError("блюдо не найдено", 404)
                db.delete_dish(dish_id)
                return 200, {"ok": True}, json_type
            finally:
                db.close()

        if path == "/api/stats" and method == "GET":
            period = _period(query.get("period", 7))
            db = self._db()
            try:
                return 200, {"ok": True, "stats": self._stats(db, period)}, json_type
            finally:
                db.close()

        match = re.fullmatch(r"/api/day/([0-9-]+)/workout", path)
        if match and method == "POST":
            date = _date(match.group(1), self._today())
            data = body if isinstance(body, dict) else {}
            workout = bool(data.get("workout"))
            note = data.get("note")
            note = " ".join(str(note).split())[:300] if note is not None else None
            db = self._db()
            try:
                db.set_workout(date, workout, note or None)
                return 200, {"ok": True, "day": self._day_payload(db, date)}, json_type
            finally:
                db.close()

        if path == "/api/goal":
            db = self._db()
            try:
                if method == "GET":
                    return 200, {"ok": True, "goal": db.get_goal()}, json_type
                data = body if isinstance(body, dict) else {}
                kcal = _float(data.get("kcal"), "ккал", maximum=20_000)
                if not kcal:
                    raise ApiError("ккал цели должно быть больше нуля")
                db.set_goal(kcal, _float(data.get("protein"), "белки", maximum=2000),
                            _float(data.get("fat"), "жиры", maximum=2000),
                            _float(data.get("carb"), "углеводы", maximum=2000))
                return 200, {"ok": True, "goal": db.get_goal()}, json_type
            finally:
                db.close()

        if path == "/api/export.csv" and method == "GET":
            return 200, self._export(), "text/csv; charset=utf-8"

        if path == "/api/ai/status" and method == "GET":
            return 200, {"ok": True, "ai": self.ai.status()}, json_type

        if path == "/api/ai/parse" and method == "POST":
            self._require_ai()
            data = body if isinstance(body, dict) else {}
            text = str(data.get("text") or "").strip()
            if not text:
                raise ApiError("напиши, что съел")
            db = self._db()
            try:
                items = self.ai.parse_food(text, self._dishes_for_ai(db))
                return 200, {"ok": True, "items": self._attach(db, items)}, json_type
            finally:
                db.close()

        if path == "/api/ai/photo" and method == "POST":
            self._require_ai()
            data = body if isinstance(body, dict) else {}
            raw = str(data.get("image") or "")
            mime = "image/jpeg"
            if raw.startswith("data:"):
                header, _, raw = raw.partition(",")
                mime = header[5:].split(";")[0] or "image/jpeg"
            try:
                image = base64.b64decode(raw, validate=False)
            except (binascii.Error, ValueError):
                raise ApiError("не разобрал картинку")
            if not image:
                raise ApiError("пустое фото")
            db = self._db()
            try:
                items = self.ai.analyze_photo(image, self._dishes_for_ai(db), mime)
                return 200, {"ok": True, "items": self._attach(db, items)}, json_type
            finally:
                db.close()

        if path == "/api/ai/apply" and method == "POST":
            data = body if isinstance(body, dict) else {}
            date = _date(data.get("date"), self._today())
            items = data.get("items")
            if not isinstance(items, list) or not items:
                raise ApiError("нечего записывать")
            db = self._db()
            try:
                stamp = self._now().strftime("%H:%M")
                saved = 0
                for item in items[:30]:
                    if not isinstance(item, dict):
                        continue
                    saved += self._insert(db, date, stamp, item)
                return 200, {"ok": True, "saved": saved, "day": self._day_payload(db, date)}, json_type
            finally:
                db.close()

        if path == "/api/ai/coach" and method == "POST":
            self._require_ai()
            db = self._db()
            try:
                period = _period((body or {}).get("period", 7) if isinstance(body, dict) else 7)
                stats = self._stats(db, period)
                summary = {
                    "период": f"{stats['start']} — {stats['end']}",
                    "дней_с_записями": stats["days_filled"],
                    "тренировок": stats["workouts"],
                    "цель_ккал": (stats["goal"] or {}).get("kcal"),
                    "среднее_ккал": stats["avg"]["kcal"],
                    "среднее_белки": stats["avg"]["protein"],
                    "среднее_жиры": stats["avg"]["fat"],
                    "среднее_углеводы": stats["avg"]["carb"],
                    "по_дням": [
                        {"дата": entry["date"], "ккал": entry["kcal"],
                         "цель": entry["goal"], "тренировка": entry["workout"]}
                        for entry in stats["by_day"] if entry["filled"]
                    ],
                }
                return 200, {"ok": True, "text": self.ai.weekly_review(summary)}, json_type
            finally:
                db.close()

        raise ApiError("такого метода нет", 404)

    # -------------------------------------------------------------- операции

    def _dishes_for_ai(self, db: DB) -> list[dict]:
        return [{"name": row["name"], "kcal": row["kcal"]} for row in db.popular_dishes(80)]

    def _attach(self, db: DB, items: list[dict]) -> list[dict]:
        """Подтягивает совпадения из базы: цифры базы точнее оценки модели."""
        for item in items:
            dish = db.find_dish_by_name(item["name"])
            if not dish:
                continue
            item["dish_id"] = dish["id"]
            item["source"] = "db"
            grams = item.get("grams")
            if grams:
                factor = float(grams) / 100.0
                item["kcal"] = round(float(dish["kcal"]) * factor, 1)
                item["protein"] = round(float(dish["protein"]) * factor, 1)
                item["fat"] = round(float(dish["fat"]) * factor, 1)
                item["carb"] = round(float(dish["carb"]) * factor, 1)
            else:
                item["grams"] = 100.0
        return items

    def _insert(self, db: DB, date: str, time: str, item: dict) -> int:
        grams = _float(item.get("grams"), "граммы", default=0.0, maximum=5000) or None
        dish_id = _int_or_none(item.get("dish_id"))
        dish = db.get_dish(dish_id) if dish_id else None
        if dish:
            factor = (grams or 100.0) / 100.0
            kcal = float(dish["kcal"]) * factor
            protein = float(dish["protein"]) * factor
            fat = float(dish["fat"]) * factor
            carb = float(dish["carb"]) * factor
            name = dish["name"]
        else:
            name = " ".join(str(item.get("name") or "").split())[:120]
            if not name:
                return 0
            kcal = _float(item.get("kcal"), "ккал", maximum=10_000)
            protein = _float(item.get("protein"), "белки", maximum=1000)
            fat = _float(item.get("fat"), "жиры", maximum=1000)
            carb = _float(item.get("carb"), "углеводы", maximum=1000)
            if not kcal and not protein and not fat and not carb:
                return 0
        db.log_meal(date, time, name, kcal, protein, fat, carb, grams=grams, dish_id=dish_id)
        return 1

    def _require_ai(self):
        """503, если модель не настроена: это не ошибка шлюза, а отсутствие услуги."""
        if not self.ai.enabled:
            raise ApiError("нейросеть выключена — включи секцию ai в config.json", 503)

    def _add_meal(self, data: dict) -> dict:
        date = _date(data.get("date"), self._today())
        grams = _float(data.get("grams"), "граммы", default=0.0, maximum=5000) or None
        dish_id = _int_or_none(data.get("dish_id"))
        time = str(data.get("time") or "").strip()
        if not re.fullmatch(r"\d{1,2}:\d{2}", time):
            time = self._now().strftime("%H:%M")
        db = self._db()
        try:
            if dish_id:
                dish = db.get_dish(dish_id)
                if not dish:
                    raise ApiError("блюдо не найдено", 404)
                item = {"dish_id": dish_id, "grams": grams or 100.0}
            else:
                item = {"name": data.get("name"), "grams": grams,
                        "kcal": data.get("kcal"), "protein": data.get("protein"),
                        "fat": data.get("fat"), "carb": data.get("carb")}
            saved = self._insert(db, date, time, item)
            if not saved:
                raise ApiError("нечего записывать: нужны блюдо или КБЖУ")
            return {"ok": True, "day": self._day_payload(db, date)}
        finally:
            db.close()

    # -------------------------------------------------------------- экспорт

    def _export(self) -> str:
        db = self._db()
        try:
            rows = db.all_meals()
            marks = {row["date"]: row for row in db.conn.execute("SELECT * FROM days")}
        finally:
            db.close()
        lines = ["\ufeffдата;время;блюдо;граммы;ккал;белки;жиры;углеводы;тренировка"]
        for row in rows:
            mark = marks.get(row["date"])
            lines.append(";".join([
                row["date"], row["time"], str(row["name"]).replace(";", ","),
                fmt.num(row["grams"]) if row["grams"] else "",
                fmt.kcal(row["kcal"]), fmt.num(row["protein"]), fmt.num(row["fat"]),
                fmt.num(row["carb"]), "да" if mark and mark["workout"] else "",
            ]))
        return "\r\n".join(lines) + "\r\n"
