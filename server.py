"""Веб-сервер мини-приложения: python server.py

Отдаёт webapp/ (HTML/CSS/JS) и JSON API в /api/*. Только стандартная библиотека.

Локально:
    python server.py --demo            # http://127.0.0.1:8080 без Telegram
На сервере (за Caddy/nginx с HTTPS):
    python server.py --host 127.0.0.1 --port 8080
Регистрация кнопки меню в Telegram:
    python server.py --register        # берёт webapp.url из config.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import bot as bot_module
from kkal.webapi import MAX_BODY, WebAPI

log = logging.getLogger("kkal.web")

ROOT = os.path.dirname(os.path.abspath(__file__))
WEBAPP_DIR = os.path.join(ROOT, "webapp")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def load_static(name: str) -> bytes | None:
    """Файл из webapp/ строго внутри каталога (без ../)."""
    path = os.path.normpath(os.path.join(WEBAPP_DIR, name))
    if not path.startswith(os.path.normpath(WEBAPP_DIR) + os.sep):
        return None
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


class Handler(BaseHTTPRequestHandler):
    server_version = "kkal-web"
    protocol_version = "HTTP/1.1"
    api: WebAPI | None = None
    demo = False

    # ------------------------------------------------------------- ответы

    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store",
              disposition: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        if self.demo:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Init-Data")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    # ------------------------------------------------------------- ввод

    def _read_body(self) -> dict | str | None:
        length = self.headers.get("Content-Length") or "0"
        try:
            size = int(length)
        except ValueError:
            size = 0
        if size <= 0:
            return None
        if size > MAX_BODY:
            raise ValueError("too large")
        raw = self.rfile.read(size)
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return None
        if "json" in (self.headers.get("Content-Type") or ""):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                raise ValueError("bad json")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _headers(self) -> dict:
        return {key.lower(): value for key, value in self.headers.items()}

    # ------------------------------------------------------------- роуты

    def _api(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        try:
            body = self._read_body()
        except ValueError as exc:
            if str(exc) == "too large":
                self._error(413, "слишком большой запрос")
            else:
                self._error(400, "не разобрал JSON в теле запроса")
            return

        api = self.api
        if api is None:
            self._error(500, "API не инициализирован")
            return
        status, payload, content_type = api.handle(method, parsed.path, query, body, self._headers())
        if content_type.startswith("application/json"):
            body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            body_bytes = str(payload).encode("utf-8")
        disposition = ""
        if parsed.path.endswith(".csv"):
            disposition = "attachment; filename=kkal.csv"
        self._send(status, body_bytes, content_type, disposition=disposition)

    def _static(self) -> None:
        parsed = urlparse(self.path)
        name = parsed.path.lstrip("/") or "index.html"
        if name in ("", "index.html"):
            name = "index.html"
        data = load_static(name)
        if data is None:
            if "." not in os.path.basename(name):
                data = load_static("index.html")  # spa-фолбэк
                name = "index.html"
            if data is None:
                self._send(404, "Не найдено".encode("utf-8"), "text/plain; charset=utf-8")
                return
        ext = os.path.splitext(name)[1].lower()
        cache = "no-cache" if ext in (".html", ".js", ".css") else "public, max-age=3600"
        self._send(200, data, MIME.get(ext, "application/octet-stream"), cache)

    # ------------------------------------------------------------- методы

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/"):
            self._api("GET")
        else:
            self._static()

    def do_HEAD(self) -> None:  # noqa: N802
        if self.path.startswith("/api/"):
            self._api("GET")
        else:
            self._static()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/api/"):
            self._api("POST")
        else:
            self._error(405, "только /api/*")

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.startswith("/api/"):
            self._api("DELETE")
        else:
            self._error(405, "только /api/*")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain; charset=utf-8")

    def log_message(self, fmt: str, *args) -> None:
        path = args[0] if args else ""
        if isinstance(path, str) and "/api/" in path:
            log.debug("%s %s", self.command, path)
        else:
            log.info("%s %s", self.command, path)


def make_server(host: str, port: int, api: WebAPI, demo: bool = False) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"api": api, "demo": demo})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def register_menu(url: str) -> None:
    from kkal.api import TelegramAPI, TelegramError

    config = bot_module.load_config()
    api = TelegramAPI(str(config["token"]))
    try:
        api._call("getMe")
        api.set_chat_menu_button(url)
        log.info("Кнопка меню привязана к %s", url)
    except TelegramError as exc:
        log.error("Не смог обновить кнопку меню: %s", exc)
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Веб-сервер мини-приложения КБЖУ")
    parser.add_argument("--config", default="config.json", help="путь к config.json")
    parser.add_argument("--host", default=None, help="по умолчанию из config.webapp.host")
    parser.add_argument("--port", type=int, default=None, help="по умолчанию из config.webapp.port")
    parser.add_argument("--db", default=None, help="путь к базе (по умолчанию из config)")
    parser.add_argument("--demo", action="store_true", help="разрешить вход без Telegram")
    parser.add_argument("--register", action="store_true", help="только обновить кнопку меню")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    bot_module.CONFIG_PATH = args.config
    bot_module.setup_logging()
    logging.getLogger().setLevel(args.log_level.upper())

    config = bot_module.load_config()
    web = config.get("webapp") or {}
    url = str(web.get("url") or "").strip()
    host = args.host or str(web.get("host") or "127.0.0.1")
    port = args.port or int(web.get("port") or 8080)
    db_path = args.db or str(config.get("db") or "data/kkal.db")
    demo = bool(args.demo or web.get("demo"))

    if args.register:
        if not url:
            print("Сначала впиши webapp.url в config.json — например https://kkal.example.com")
            raise SystemExit(1)
        register_menu(url)
        return

    api = WebAPI(db_path, str(config["token"]), config, demo=demo)
    if api.ai.enabled:
        log.info("ИИ включён: модель %s", api.ai.model)
    else:
        log.info("ИИ выключен — работает только ручной ввод")

    server = make_server(host, port, api, demo=demo)
    shown = url or f"http://{host}:{port}"
    log.info("Мини-приложение: %s (API /api/*, база %s)", shown, db_path)
    if demo:
        log.info("Демо-режим: вход без initData разрешён — не оставляй так на сервере")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
