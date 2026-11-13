"""ИИ-слой: текст и фото еды -> записи КБЖУ, плюс недельный разбор.

Работает с любым OpenAI-совместимым API (OpenAI, OpenRouter, DeepSeek, Groq,
Together, локальный Ollama/LM Studio) — достаточно base_url + model.
Своих зависимостей нет: обычный urllib.

Конфиг в config.json:
    "ai": {
        "enabled": true,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-...",
        "model": "openai/gpt-4o-mini",
        "vision_model": "openai/gpt-4o-mini",
        "timeout": 60
    }
Для локального Ollama: "base_url": "http://127.0.0.1:11434/v1", "api_key": "".
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request

MAX_IMAGE_BYTES = 4_500_000  # Telegram отдаёт фото до ~5 МБ, больше модели не нужны


class AIError(Exception):
    """Модель недоступна или ответила мусором."""


# ------------------------------------------------------------------ транспорт


def urllib_transport(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise AIError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AIError(f"нет связи с моделью: {exc.reason}") from exc
    except TimeoutError as exc:
        raise AIError("модель не ответила вовремя") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIError(f"ответ не JSON: {raw[:200]}") from exc


# ------------------------------------------------------------------ парсинг


def extract_json(text: str) -> dict:
    """Достаёт JSON-объект из ответа модели, даже если он в ```-блоке."""
    if not text:
        raise AIError("пустой ответ модели")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise AIError(f"модель ответила не по формату: {text[:200]}")


def _number(value, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        cleaned = str(value).replace(",", ".").replace("%", "").strip()
        cleaned = re.sub(r"[^\d.\-]", "", cleaned)
        if not cleaned:
            return default
        try:
            number = float(cleaned)
        except ValueError:
            return default
    return number if number > 0 else 0.0


def clean_items(raw: dict | list) -> list[dict]:
    """Приводит ответ модели к списку позиций с числами и ограничениями."""
    items = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise AIError("в ответе модели нет списка items")
    result: list[dict] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        name = " ".join(str(entry.get("name") or "").split())[:120]
        if not name:
            continue
        grams = entry.get("grams")
        grams_value = _number(grams) if grams not in (None, "") else None
        if grams_value is not None and grams_value > 5000:
            grams_value = 5000.0
        kcal = min(_number(entry.get("kcal")), 10000.0)
        item = {
            "name": name,
            "grams": round(grams_value, 1) if grams_value else None,
            "kcal": round(kcal, 1),
            "protein": round(min(_number(entry.get("protein")), 1000.0), 1),
            "fat": round(min(_number(entry.get("fat")), 1000.0), 1),
            "carb": round(min(_number(entry.get("carb")), 1000.0), 1),
            "source": "ai",
            "note": " ".join(str(entry.get("note") or "").split())[:200],
        }
        if not item["kcal"] and not item["protein"] and not item["fat"] and not item["carb"]:
            continue
        result.append(item)
    return result


# ------------------------------------------------------------------ сам ИИ


TEXT_SYSTEM = (
    "Ты — диетологический помощник в приложении для учёта КБЖУ. "
    "Пользователь пишет по-русски, что он съел. Разбей описание на отдельные блюда "
    "и верни ТОЛЬКО JSON без пояснений:\n"
    '{"items":[{"name":"...","grams":150,"kcal":330,"protein":27,"fat":13.5,"carb":36}]}\n'
    "Правила:\n"
    "- kcal/protein/fat/carb — на указанную порцию, не на 100 г.\n"
    "- Если вес не указан, подставь типичную порцию и укажи её в grams.\n"
    "- Если блюдо есть в списке известных, назови его точно как в списке.\n"
    "- Не выдумывай лишние позиции; воду и чай без сахара пропускай.\n"
    "- Числа — обычные, через точку."
)

PHOTO_SYSTEM = (
    "Ты — диетологический помощник. На фото еда. Определи блюда и оцени порцию. "
    "Верни ТОЛЬКО JSON без пояснений:\n"
    '{"items":[{"name":"...","grams":150,"kcal":330,"protein":27,"fat":13.5,"carb":36}]}\n'
    "Правила:\n"
    "- kcal/protein/fat/carb — на видимую порцию целиком.\n"
    "- grams — оценка веса порции по фото.\n"
    "- Если на фото несколько блюд — отдельные позиции.\n"
    "- В note коротко объясни, как оценил (одна фраза, по-русски).\n"
    "- Если на фото не еда, верни {\"items\":[]}."
)

COACH_SYSTEM = (
    "Ты — спокойный и конкретный тренер по питанию. По данным дневника дай разбор "
    "на русском: 3–6 предложений. Скажи, что получилось, где перебор или недобор, "
    "как влияют тренировки, и дай 1–2 конкретных совета на следующую неделю. "
    "Без воды, без списков, без медицинских диагнозов. Обращайся на «ты»."
)


class AI:
    def __init__(self, config: dict | None = None, transport=None):
        cfg = config or {}
        self.base_url = str(cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = str(cfg.get("api_key") or "")
        self.model = str(cfg.get("model") or "gpt-4o-mini")
        self.vision_model = str(cfg.get("vision_model") or self.model)
        self.timeout = float(cfg.get("timeout") or 60)
        self.transport = transport or urllib_transport
        self._explicit = cfg.get("enabled")
        self._local = self.base_url.startswith(("http://127.0.0.1", "http://localhost",
                                                "http://[::1]", "http://0.0.0.0"))

    # -------------------------------------------------------------- статус

    @property
    def enabled(self) -> bool:
        if self._explicit is False:
            return False
        if self._explicit is True:
            return bool(self.base_url)
        return bool(self.api_key) or self._local

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "model": self.model if self.enabled else "",
            "vision": bool(self.enabled and self.vision_model),
            "local": self._local,
        }

    # -------------------------------------------------------------- вызов

    def _chat(self, messages: list[dict], model: str, json_mode: bool = True,
              temperature: float = 0.2, max_tokens: int = 900) -> str:
        if not self.enabled:
            raise AIError("ИИ не настроен: добавь секцию \"ai\" в config.json")
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = self.transport(self.base_url + "/chat/completions", headers, payload, self.timeout)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AIError(f"неожиданный ответ API: {str(data)[:200]}")
        if isinstance(content, list):  # некоторые провайдеры отдают блоки
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content or "")

    # -------------------------------------------------------------- сценарии

    def parse_food(self, text: str, dishes: list[dict] | None = None) -> list[dict]:
        text = " ".join(str(text or "").split())
        if not text:
            raise AIError("пустой текст")
        known = _known_dishes(dishes or [])
        user = f"Что я съел: {text}"
        if known:
            user += "\n\nИзвестные блюда (название — ккал/100 г):\n" + known
        answer = self._chat(
            [{"role": "system", "content": TEXT_SYSTEM}, {"role": "user", "content": user}],
            model=self.model,
        )
        return clean_items(extract_json(answer))

    def analyze_photo(self, image_bytes: bytes, dishes: list[dict] | None = None,
                      mime: str = "image/jpeg") -> list[dict]:
        if not image_bytes:
            raise AIError("пустое фото")
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise AIError("фото слишком большое, сожми до ~4 МБ")
        source = "data:" + (mime or "image/jpeg") + ";base64," + base64.b64encode(image_bytes).decode("ascii")
        known = _known_dishes(dishes or [])
        hint = ""
        if known:
            hint = "\n\nИзвестные блюда из базы (название — ккал/100 г):\n" + known
        answer = self._chat(
            [
                {"role": "system", "content": PHOTO_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Оцени КБЖУ по фото." + hint},
                    {"type": "image_url", "image_url": {"url": source}},
                ]},
            ],
            model=self.vision_model,
            max_tokens=900,
        )
        return clean_items(extract_json(answer))

    def weekly_review(self, summary: dict) -> str:
        answer = self._chat(
            [
                {"role": "system", "content": COACH_SYSTEM},
                {"role": "user", "content": "Данные дневника:\n"
                 + json.dumps(summary, ensure_ascii=False, indent=1)},
            ],
            model=self.model,
            json_mode=False,
            temperature=0.4,
            max_tokens=600,
        )
        text = " ".join(answer.split())
        if not text:
            raise AIError("модель вернула пустой разбор")
        return text[:1500]


def _known_dishes(dishes: list[dict], limit: int = 80) -> str:
    lines = []
    for dish in dishes[:limit]:
        name = " ".join(str(dish.get("name") or "").split())
        if not name:
            continue
        lines.append(f"- {name} — {round(float(dish.get('kcal') or 0))}")
    return "\n".join(lines)
