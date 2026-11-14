"""Автотест мини-приложения без сети и без HTTP-сервера.

Проверяет подпись initData, JSON API (день, блюда, статистику, цели, экспорт)
и слой ИИ на поддельной модели. Запуск:  python selftest_web.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from urllib.parse import urlencode

from kkal.ai import AI
from kkal.auth import calc_hash
from kkal.webapi import WebAPI

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOKEN = "123456789:SELFTEST-TOKEN"
HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "data", "_selftest_web")
DB_PATH = os.path.join(DIR, "kkal.db")

FAILS: list[str] = []


def check(condition: bool, label: str) -> None:
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}")
        FAILS.append(label)


def head(text: str) -> None:
    print(f"\n{text}")


def fresh_db() -> None:
    if os.path.isdir(DIR):
        shutil.rmtree(DIR, ignore_errors=True)
    os.makedirs(DIR, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        path = DB_PATH + suffix
        if os.path.exists(path):
            os.remove(path)


# ------------------------------------------------------------------ модель


class FakeModel:
    """Поддельный OpenAI-совместимый сервер: считает вызовы, отдаёт заготовки."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url: str, headers: dict, payload: dict, timeout: float) -> dict:
        self.calls.append({"url": url, "payload": payload})
        messages = payload.get("messages") or []
        last = messages[-1].get("content") if messages else ""
        if isinstance(last, list):  # фото
            return {"choices": [{"message": {"content": json.dumps({"items": [
                {"name": "Гречка отварная", "grams": 200, "kcal": 220,
                 "protein": 8, "fat": 2, "carb": 40, "note": "порция на фото"},
                {"name": "Куриная грудка", "grams": 150, "kcal": 250,
                 "protein": 45, "fat": 5, "carb": 0},
            ]}, ensure_ascii=False)}}]}
        if "response_format" not in payload:  # коуч
            return {"choices": [{"message": {"content": "  Нормальный темп, добавь овощей.  "}}]}
        return {"choices": [{"message": {"content": json.dumps({"items": [
            {"name": "Сырники", "grams": 150, "kcal": 400, "protein": 20,
             "fat": 14, "carb": 40},
        ]}, ensure_ascii=False)}}]}


# ------------------------------------------------------------------ помощники


def make_api(demo: bool = False, owner=0, ai: AI | None = None) -> WebAPI:
    config = {
        "token": TOKEN,
        "owner_id": owner,
        "utc_offset": 4,
        "db": DB_PATH,
        "ai": {"enabled": False},
    }
    return WebAPI(DB_PATH, TOKEN, config, demo=demo, ai=ai)


def call(api: WebAPI, method: str, path: str, body=None, init: str | None = "",
         query: dict | None = None):
    headers = {}
    if init:
        headers["x-init-data"] = init
    status, payload, _ = api.handle(method, path, query or {}, body, headers)
    return status, payload


def signed(user_id: int = 777, username: str = "robchik69") -> str:
    from kkal.auth import sign_init_data
    return sign_init_data(TOKEN, user_id, username)


def ai_stub() -> AI:
    model = FakeModel()
    ai = AI({"enabled": True, "base_url": "http://127.0.0.1:11434/v1",
             "model": "test-model", "vision_model": "test-vision"}, transport=model)
    ai.model_calls = model.calls  # type: ignore[attr-defined]
    return ai


# ------------------------------------------------------------------ сценарии


def scenario_auth() -> None:
    head("Подпись initData")
    api = make_api(demo=False)

    status, payload = call(api, "GET", "/api/health", init=None)
    check(status == 200 and payload.get("ok"), "health отвечает без входа")

    status, payload = call(api, "GET", "/api/session", init=None)
    check(status == 401, "без initData сессии нет")

    status, payload = call(api, "GET", "/api/session", init=signed()[:-4] + "beef")
    check(status == 401 and "подпись" in payload.get("error", ""), "битая подпись отклонена")

    stale = signed()
    status, payload = call(api, "GET", "/api/session", init=stale)
    check(status == 200, "свежая подпись принимается")

    user = json.dumps({"id": 777, "first_name": "Тест"}, separators=(",", ":"), ensure_ascii=False)
    pairs = [("auth_date", str(int(time.time()) - 100_000)), ("query_id", "AA0"), ("user", user)]
    from kkal.auth import sign_init_data
    old = sign_init_data(TOKEN, 777, auth_date=int(time.time()) - 100_000)
    status, payload = call(api, "GET", "/api/session", init=old)
    check(status == 401 and "устарел" in payload.get("error", ""), "старый auth_date отклонён")

    extra = [("auth_date", str(int(time.time()))), ("query_id", "AA0"),
             ("signature", "deadbeef"), ("user", user)]
    extra.append(("hash", calc_hash(TOKEN, extra)))
    status, payload = call(api, "GET", "/api/session", init=urlencode(extra))
    check(status == 200 and payload["user"]["id"] == 777,
          "поле signature в initData не мешает проверке")

    status, payload = call(api, "POST", "/api/session", body={"initData": signed()})
    check(status == 200, "initData можно передать телом запроса")
    check(payload["day"]["is_today"] is True and payload["tz_offset"] == 4.0,
          "сессия отдаёт сегодняшний день и UTC+4")

    owner_api = make_api(owner=777)
    status, payload = call(owner_api, "GET", "/api/session", init=signed(888, "чужой"))
    check(status == 403 and "личный" in payload.get("error", ""), "чужой не пройдёт по owner_id")
    status, _ = call(owner_api, "GET", "/api/session", init=signed(777, "robchik69"))
    check(status == 200, "владелец по id проходит")

    by_name = make_api(owner="@Robchik69")
    status, _ = call(by_name, "GET", "/api/session", init=signed(555, "robchik69"))
    check(status == 200, "владелец по username проходит")

    demo_api = make_api(demo=True)
    status, payload = call(demo_api, "GET", "/api/session", init=None)
    check(status == 200 and payload["demo"] is True, "демо-режим пускает без initData")
    status, _ = call(demo_api, "GET", "/api/session", init=signed()[:-4] + "beef")
    check(status == 401, "в демо-режиме битая подпись всё равно отклонена")

    demo_owner = make_api(demo=True, owner=777)
    status, payload = call(demo_owner, "GET", "/api/day", init=None)
    check(status == 200, "демо-режим открыт и при заданном owner_id")


def scenario_day(api: WebAPI) -> None:
    head("День, блюда и записи")
    init = signed()

    status, payload = call(api, "POST", "/api/dishes", init=init,
                           body={"name": "Сырники", "kcal": 220, "protein": 14,
                                 "fat": 10, "carb": 25})
    check(status == 200 and payload["created"] is True, "блюдо добавлено в базу")
    dish_id = payload["dish"]["id"]

    status, payload = call(api, "POST", "/api/dishes", init=init,
                           body={"name": "Сырники", "kcal": 220, "protein": 14,
                                 "fat": 10, "carb": 25})
    check(status == 200 and payload["created"] is False and payload["dish"]["id"] == dish_id,
          "повторное имя обновляет блюдо, а не дублирует")

    status, payload = call(api, "GET", "/api/dishes", init=init, query={"q": "сыр"})
    check(status == 200 and any(item["name"].lower() == "сырники" for item in payload["items"]),
          "поиск по базе находит блюдо")

    status, payload = call(api, "GET", "/api/dishes", init=init)
    check(status == 200 and payload["total"] >= 1, "список блюд отдаётся без запроса")

    status, payload = call(api, "POST", "/api/dishes", init=init, body={"name": "  "})
    check(status == 400 and "название" in payload["error"], "пустое название отклонено")

    status, payload = call(api, "POST", "/api/meals", init=init,
                           body={"dish_id": dish_id, "grams": 150})
    check(status == 200 and payload["day"]["totals"]["kcal"] == 330,
          "150 г блюда на 220 ккал → 330")

    status, payload = call(api, "POST", "/api/meals", init=init,
                           body={"name": "Кофе с молоком", "kcal": 60, "protein": 3,
                                 "fat": 3, "carb": 5})
    check(status == 200 and payload["day"]["totals"]["kcal"] == 390,
          "разовая запись суммируется с днём")
    check(len(payload["day"]["meals"]) == 2, "в дне две записи")
    check(payload["day"]["label"] == "сегодня", "день подписан как «сегодня»")
    meal_id = payload["day"]["meals"][-1]["id"]

    status, payload = call(api, "POST", "/api/meals", init=init,
                           body={"dish_id": dish_id, "grams": -5})
    check(status == 400 and "граммы" in payload["error"], "отрицательные граммы отклонены")

    status, payload = call(api, "POST", "/api/meals", init=init, body={"dish_id": 999999})
    check(status == 404, "несуществующее блюдо → 404")

    status, payload = call(api, "POST", "/api/meals", init=init, body={"grams": 100})
    check(status == 400 and "нечего" in payload["error"], "пустая разовая запись отклонена")

    status, payload = call(api, "DELETE", f"/api/meals/{meal_id}", init=init)
    check(status == 200 and payload["day"]["totals"]["kcal"] == 330,
          "удаление записи пересчитывает день")

    status, payload = call(api, "DELETE", "/api/meals/999999", init=init)
    check(status == 404, "удаление несуществующей записи → 404")

    status, payload = call(api, "POST", "/api/day/" + payload_day(api) + "/workout",
                           init=init, body={"workout": True, "note": "зал"})
    check(status == 200 and payload["day"]["workout"] is True, "тренировка отмечается")
    check(payload["day"]["note"] == "зал", "заметка к дню сохраняется")

    status, payload = call(api, "POST", "/api/goal", init=init,
                           body={"kcal": 2000, "protein": 120, "fat": 70, "carb": 200})
    check(status == 200 and payload["goal"]["kcal"] == 2000, "цель по ккал сохраняется")

    status, payload = call(api, "POST", "/api/goal", init=init, body={"kcal": 0})
    check(status == 400 and "больше нуля" in payload["error"], "нулевая цель отклонена")

    status, payload = call(api, "GET", "/api/day", init=init)
    check(status == 200 and payload["day"]["goal"]["protein"] == 120,
          "цель видна в дне")
    check(payload["day"]["totals"]["protein"] == 21, "белки считаются по граммам")

    status, payload = call(api, "GET", "/api/day", init=init, query={"date": "вчера"})
    check(status == 400, "кривая дата отклонена")


def payload_day(api: WebAPI) -> str:
    return api._today()


def scenario_stats(api: WebAPI) -> None:
    head("Статистика")
    init = signed()

    status, payload = call(api, "GET", "/api/stats", init=init, query={"period": "7"})
    stats = payload["stats"]
    check(status == 200 and len(stats["by_day"]) == 7, "неделя — семь точек на графике")
    check(stats["days_filled"] == 1, "заполнен один день")
    check(stats["workouts"] == 1, "одна тренировка за неделю")
    check(stats["by_day"][-1]["is_today"] if "is_today" in stats["by_day"][-1] else
          stats["by_day"][-1]["date"] == api._today(), "последняя точка — сегодня")
    check(stats["avg"]["kcal"] == 330, "среднее считается по дням с записями")
    check(stats["totals"]["kcal"] == 330, "сумма за период")
    check(len(stats["weeks"]) == 1 and stats["weeks"][0]["days"] == 1,
          "недельный срез собран")

    status, payload = call(api, "GET", "/api/stats", init=init, query={"period": "0"})
    check(payload["stats"]["start"] == payload["stats"]["first_date"],
          "период «всё» начинается с первой записи")

    status, payload = call(api, "GET", "/api/stats", init=init, query={"period": "мусор"})
    check(len(payload["stats"]["by_day"]) == 7, "мусор в period → неделя по умолчанию")

    status, body = call(api, "GET", "/api/export.csv", init=init)
    check(status == 200 and isinstance(body, str), "CSV отдаётся строкой")
    check(body.startswith("\ufeffдата;время;блюдо"), "CSV с BOM и шапкой")
    check("Сырники" in body, "CSV содержит запись")


def scenario_ai(api: WebAPI, init: str) -> None:
    head("ИИ")
    status, payload = call(api, "POST", "/api/ai/parse", init=init, body={"text": "съел сырники"})
    check(status == 503 and "нейросеть" in payload["error"], "без модели ИИ честно отвечает 503")

    status, payload = call(api, "POST", "/api/ai/coach", init=init, body={"period": 7})
    check(status == 503, "коуч без модели тоже 503, а не 500")

    broken = make_api(ai=AI({"enabled": True, "base_url": "http://127.0.0.1:1/v1",
                             "api_key": "test", "timeout": 2}))
    status, payload = call(broken, "POST", "/api/ai/parse", init=init, body={"text": "съел сырники"})
    check(status == 502 and payload["error"].startswith("ИИ:"), "сбой модели — 502 с текстом ошибки")

    status, payload = call(api, "GET", "/api/ai/status", init=init)
    check(status == 200 and payload["ai"]["enabled"] is False, "статус ИИ: выключен")

    smart = make_api(ai=ai_stub())
    model = smart.ai

    status, payload = call(smart, "POST", "/api/ai/parse", init=init,
                           body={"text": "съел 150 г сырников"})
    check(status == 200 and len(payload["items"]) == 1, "текст разобран в позиции")
    item = payload["items"][0]
    check(item["source"] == "db" and item["dish_id"], "блюдо из базы подставлено по имени")
    check(item["kcal"] == 330, "калории пересчитаны из базы, а не из ответа модели")

    status, payload = call(smart, "POST", "/api/ai/parse", init=init, body={"text": ""})
    check(status == 400 and "напиши" in payload["error"], "пустой текст отклонён")

    status, payload = call(smart, "POST", "/api/ai/photo", init=init,
                           body={"image": "data:image/png;base64," + "iVBORw0KGgo="})
    check(status == 200 and len(payload["items"]) == 2, "фото разобрано в две позиции")
    check(payload["items"][1]["source"] == "ai", "незнакомое блюдо остаётся оценкой ИИ")
    photo_call = smart.ai.model_calls[-1]["payload"]
    content = photo_call["messages"][-1]["content"]
    check(any(part.get("type") == "image_url" and part["image_url"]["url"].startswith("data:image/png")
              for part in content), "фото уходит в модель как data-url")
    check(photo_call.get("model") == "test-vision", "для фото берётся vision-модель")

    status, payload = call(smart, "POST", "/api/ai/photo", init=init, body={"image": ""})
    check(status == 400 and "фото" in payload["error"], "пустое фото отклонено")

    status, payload = call(smart, "POST", "/api/ai/apply", init=init,
                           body={"items": payload_items(smart, init)})
    check(status == 200 and payload["saved"] == 2, "позиции ИИ записаны в дневник")
    check(payload["day"]["totals"]["kcal"] > 330, "дневник вырос после записи от ИИ")

    status, payload = call(smart, "POST", "/api/ai/apply", init=init, body={"items": []})
    check(status == 400 and "нечего" in payload["error"], "пустой список не пишется")

    status, payload = call(smart, "POST", "/api/ai/coach", init=init, body={"period": 7})
    check(status == 200 and payload["text"].startswith("Нормальный темп"), "коуч отвечает текстом")
    coach_call = smart.ai.model_calls[-1]["payload"]
    check("response_format" not in coach_call, "коуч просит обычный текст, а не JSON")
    check("цель_ккал" in coach_call["messages"][-1]["content"], "в сводку попала цель")


def payload_items(api: WebAPI, init: str) -> list[dict]:
    status, payload = call(api, "POST", "/api/ai/photo", init=init,
                           body={"image": "data:image/png;base64,iVBORw0KGgo="})
    return payload["items"] if status == 200 else []


def scenario_http_smoke() -> None:
    head("HTTP-сервер")
    try:
        import server
    except Exception as exc:  # pragma: no cover
        check(False, f"server.py импортируется ({exc})")
        return
    check(hasattr(server, "make_server"), "server.py собирает ThreadingHTTPServer")
    check(server.MIME[".js"].startswith("text/javascript"), "MIME для JS")
    check(server.load_static("index.html") is not None, "index.html на месте")
    check(server.load_static("../bot.py") is None, "выход за webapp/ запрещён")
    check(server.load_static("app.css") is not None, "app.css на месте")
    check(server.load_static("app.js") is not None, "app.js на месте")


def scenario_http_live() -> None:
    """Настоящий HTTP-сервер на 127.0.0.1:0 — проверяем статику, API и заголовки."""
    head("HTTP вживую")
    import threading
    import urllib.error
    import urllib.request

    import server as srv

    httpd = srv.make_server("127.0.0.1", 0, make_api(demo=True), demo=True)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def fetch(path: str, method: str = "GET", body=None, headers: dict | None = None):
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        request = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    try:
        status, response_headers, body = fetch("/")
        check(status == 200 and b"app.js" in body, "index.html отдаётся по /")
        check(response_headers.get("Cache-Control") == "no-cache", "HTML не кешируется")
        status, _, body = fetch("/app.js")
        check(status == 200 and b"data-act" in body, "app.js отдаётся")
        status, _, body = fetch("/today")
        check(status == 200 and b'id="app"' in body, "неизвестный путь — SPA-фолбэк")
        status, _, body = fetch("/api/../bot.py")
        check(status in (404, 400), "выход за webapp/ в HTTP закрыт")

        status, _, body = fetch("/api/session")
        payload = json.loads(body.decode("utf-8"))
        check(status == 200 and payload.get("demo") is True, "сессия в демо-режиме")
        status, response_headers, _ = fetch("/api/session")
        check(response_headers.get(
            "Access-Control-Allow-Origin") == "*", "CORS только в демо-режиме")

        status, _, body = fetch("/api/session", headers={
            "X-Telegram-Init-Data": "hash=deadbeef&auth_date=1"})
        check(status == 401, "битый initData — 401")

        status, _, body = fetch("/api/nope")
        check(status == 404 and json.loads(body.decode("utf-8"))["ok"] is False,
              "неизвестный /api — 404 с ошибкой")

        status, _, body = fetch("/api/dishes", method="POST", body={
            "name": "Котлета по-киевски", "kcal": 300, "protein": 20, "fat": 20, "carb": 10})
        dish = json.loads(body.decode("utf-8")).get("dish") or {}
        check(status == 200 and dish.get("id"), "блюдо создано через HTTP")

        today = "2020-01-01"
        status, _, body = fetch("/api/meals", method="POST", body={
            "date": today, "dish_id": dish.get("id"), "grams": 200})
        day = json.loads(body.decode("utf-8")).get("day") or {}
        check(status == 200 and round(day["totals"]["kcal"]) == 600, "запись через HTTP пересчитана по базе")

        status, response_headers, body = fetch("/api/export.csv")
        check(status == 200 and "attachment" in (response_headers.get("Content-Disposition") or ""),
              "CSV отдаётся файлом")
        check("Котлета" in body.decode("utf-8"), "в CSV попало блюдо")

        status, response_headers, _ = fetch("/api/health", method="OPTIONS")
        check(status == 204, "OPTIONS отвечает 204")

        status, response_headers, body = fetch("/api/health", method="HEAD")
        check(status == 200 and body == b"", "HEAD отдаёт заголовки без тела")
        check(response_headers.get("Content-Type", "").startswith("application/json"),
              "у HEAD правильный Content-Type")
    finally:
        httpd.shutdown()
        httpd.server_close()


def main() -> None:
    fresh_db()
    print("Проверяю мини-приложение без сети")
    scenario_auth()
    api = make_api()
    scenario_day(api)
    scenario_stats(api)
    scenario_ai(api, signed())
    scenario_http_smoke()
    scenario_http_live()

    total = 0
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} проверок упало:")
        for label in FAILS:
            print(f"   • {label}")
        raise SystemExit(1)
    print("✅ Все проверки прошли")


if __name__ == "__main__":
    main()
