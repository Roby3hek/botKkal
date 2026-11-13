"""Проверка initData от Telegram Mini App и подпись тестовых сессий.

Telegram отдаёт мини-приложению строку initData вида
    query_id=...&user=%7B%22id%22%3A1%7D&auth_date=1700000000&hash=...
Подпись считается так:
    secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
    hash       = HMAC_SHA256(key=secret_key,    msg=data_check_string)

data_check_string — все пары кроме hash, отсортированные по имени и склеенные
через перевод строки. Значения берём уже URL-декодированными.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, urlencode

SESSION_TTL = 24 * 60 * 60  # сутки: мини-апп редко живёт дольше


class AuthError(Exception):
    """initData не прошёл проверку — запрос надо отклонить."""


def signing_key(token: str) -> bytes:
    return hmac.new(b"WebAppData", str(token).encode("utf-8"), hashlib.sha256).digest()


def data_check_string(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(pairs) if key != "hash")


def calc_hash(token: str, pairs: list[tuple[str, str]]) -> str:
    return hmac.new(signing_key(token), data_check_string(pairs).encode("utf-8"), hashlib.sha256).hexdigest()


def parse_init_data(init_data: str, token: str, ttl: int = SESSION_TTL,
                    now_ts: float | None = None) -> dict:
    """Возвращает {'user': {...}, 'auth_date': int, 'query_id': str}."""
    if not init_data or not str(init_data).strip():
        raise AuthError("пустой initData")
    if not token:
        raise AuthError("в конфиге нет токена бота")

    pairs = parse_qsl(str(init_data).strip(), keep_blank_values=True, strict_parsing=False)
    params = dict(pairs)
    received = params.get("hash", "")
    if not received:
        raise AuthError("в initData нет hash")
    if not hmac.compare_digest(calc_hash(token, pairs), received):
        raise AuthError("подпись initData не совпала")

    try:
        auth_date = int(params.get("auth_date") or 0)
    except ValueError:
        raise AuthError("кривой auth_date")
    if ttl and auth_date and (now_ts or time.time()) - auth_date > ttl:
        raise AuthError("initData устарел, переоткрой мини-апп")

    try:
        user = json.loads(params.get("user") or "{}")
    except json.JSONDecodeError:
        raise AuthError("не разобрал поле user")
    if not isinstance(user, dict):
        raise AuthError("поле user не объект")

    return {"user": user, "auth_date": auth_date, "query_id": params.get("query_id", "")}


def sign_init_data(token: str, user_id: int, username: str = "",
                   first_name: str = "Тест", auth_date: int | None = None) -> str:
    """Собирает валидный initData — для локальных тестов и режима разработки."""
    user = {"id": int(user_id), "first_name": first_name, "language_code": "ru"}
    if username:
        user["username"] = username
    pairs = [
        ("auth_date", str(int(auth_date if auth_date is not None else time.time()))),
        ("query_id", "AA" + "0" * 20),
        ("user", json.dumps(user, separators=(",", ":"), ensure_ascii=False)),
    ]
    digest = calc_hash(token, pairs)
    return urlencode(pairs + [("hash", digest)])


def user_id_from(init_data: str, token: str, ttl: int = SESSION_TTL) -> int:
    """Короткий путь: только id пользователя."""
    return int(parse_init_data(init_data, token, ttl)["user"].get("id") or 0)
