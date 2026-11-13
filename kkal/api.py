"""Минимальный клиент Telegram Bot API без внешних зависимостей.

Используется только стандартная библиотека (urllib), чтобы бота можно было
запустить на любой машине с Python 3.9+ без pip install.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
import uuid

log = logging.getLogger(__name__)


class TelegramError(Exception):
    """Ошибка Telegram API (ok=false) или сетевой сбой."""

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


class TelegramAPI:
    def __init__(self, token: str, request_timeout: float = 30.0):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}/"
        self.request_timeout = request_timeout

    # ---------------------------------------------------------------- ядро

    def _call(self, method: str, payload: dict | None = None, timeout: float | None = None) -> object:
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + method,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.request_timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            # Тело ответа всё равно содержит описание ошибки в JSON.
            raw = exc.read()
            try:
                info = json.loads(raw.decode("utf-8"))
            except Exception:
                raise TelegramError(f"HTTP {exc.code}: {exc.reason}", exc.code) from exc
            desc = info.get("description", f"HTTP {exc.code}")
            raise TelegramError(desc, int(info.get("error_code") or exc.code)) from exc
        except urllib.error.URLError as exc:
            raise TelegramError(f"сеть: {exc.reason}") from exc

        try:
            info = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise TelegramError("некорректный ответ Telegram") from exc

        if not info.get("ok"):
            raise TelegramError(
                info.get("description", "unknown error"),
                int(info.get("error_code") or 0),
            )
        return info.get("result")

    # ------------------------------------------------------------- методы

    def get_updates(self, offset: int, long_poll: int = 30) -> list[dict]:
        result = self._call(
            "getUpdates",
            {"offset": offset, "timeout": long_poll, "allowed_updates": ["message", "callback_query"]},
            timeout=long_poll + 20,
        )
        return result or []

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", payload)

    def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> None:
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        else:
            payload["reply_markup"] = {"inline_keyboard": []}
        try:
            self._call("editMessageText", payload)
        except TelegramError as exc:
            # «message is not modified» — нормальная ситуация, не считаем ошибкой.
            if "not modified" not in str(exc):
                raise

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        payload: dict = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        try:
            self._call("answerCallbackQuery", payload)
        except TelegramError as exc:
            log.warning("answerCallbackQuery: %s", exc)

    def send_document(self, chat_id: int, path: str, caption: str = "") -> None:
        boundary = "----kkal" + uuid.uuid4().hex
        filename = os.path.basename(path)
        with open(path, "rb") as fh:
            content = fh.read()

        def field(name: str, value: str) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")

        body = bytearray()
        body += field("chat_id", str(chat_id))
        if caption:
            body += field("caption", caption)
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            f"Content-Type: text/csv; charset=utf-8\r\n\r\n"
        ).encode("utf-8")
        body += content
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request(
            self.base + "sendDocument",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                info = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise TelegramError(f"sendDocument: HTTP {exc.code}", exc.code) from exc
        if not info.get("ok"):
            raise TelegramError(info.get("description", "sendDocument failed"))

    def set_my_commands(self, commands: list[tuple[str, str]]) -> None:
        payload = {"commands": [{"command": c, "description": d} for c, d in commands]}
        try:
            self._call("setMyCommands", payload)
        except TelegramError as exc:
            log.warning("setMyCommands: %s", exc)

    def set_chat_menu_button(self, url: str, text: str = "Открыть дневник") -> None:
        """Кнопка меню слева от поля ввода открывает мини-приложение."""
        payload = {
            "menu_button": {
                "type": "web_app",
                "text": text[:64],
                "web_app": {"url": url},
            }
        }
        self._call("setChatMenuButton", payload)

    def delete_chat_menu_button(self) -> None:
        self._call("setChatMenuButton", {"menu_button": {"type": "default"}})

    # ------------------------------------------------------- файлы (фото еды)

    def get_file(self, file_id: str) -> str:
        info = self._call("getFile", {"file_id": file_id})
        path = (info or {}).get("file_path") if isinstance(info, dict) else None
        if not path:
            raise TelegramError("getFile: нет file_path")
        return str(path)

    def download_file(self, file_path: str, timeout: float = 60.0) -> bytes:
        url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise TelegramError(f"download: HTTP {exc.code}", exc.code) from exc
        except urllib.error.URLError as exc:
            raise TelegramError(f"download: сеть: {exc.reason}") from exc
