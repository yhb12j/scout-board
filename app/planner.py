"""План сбора: модель возвращает JSON, проверка запроса решает, можно ли собирать."""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from app.gnews_client import FREE_MAX, public_url

ALLOWED_FIELDS = ["title", "date", "source", "url"]
# Бесплатный GNews отдаёт статьи с задержкой и только за 30 дней.
FREE_DELAY = timedelta(hours=12)
FREE_HISTORY = timedelta(days=30)

IMPOSSIBLE_MARKERS = (
    "закрыт",
    "приватн",
    "конкурент",
    "секретн",
    "внутренн",
)
AMBIGUOUS_MARKERS = (
    "самое важное",
    "самое важн",
    "сделай вывод",
    "по рынку",
    "покажи лучшее",
    "что-нибудь",
    "что нибудь",
)

KEYWORD_RE = re.compile(r"[«\"]([^»\"]{2,180})[»\"]")
HOURS_RE = re.compile(r"(\d+)\s*час")
DAYS_RE = re.compile(r"(\d+)\s*(?:дн|сут)")
COUNT_RES = (
    re.compile(r"последн(?:ие|их)\s+(\d+)\s+новост"),
    re.compile(r"(\d+)\s+новост"),
    re.compile(r"(\d+)\s+запис"),
)


@dataclass
class Plan:
    plan_steps: list[str]
    api_url: str
    fields_to_keep: list[str]
    confidence: str
    needs_review: bool
    reason: str = ""
    hints: list[str] = field(default_factory=list)
    model_used: bool = False
    keyword: str = ""
    search_params: dict = field(default_factory=dict)
    llm_raw: dict | None = None
    llm_error: str | None = None
    quality_note: str = ""

    def public(self) -> dict:
        return {
            "plan_steps": self.plan_steps,
            "api_url": self.api_url,
            "fields_to_keep": self.fields_to_keep,
            "confidence": self.confidence,
            "needs_review": self.needs_review,
            "reason": self.reason,
            "hints": self.hints,
            "model_used": self.model_used,
        }

    def stored(self) -> dict:
        data = self.public()
        data["llm_raw"] = self.llm_raw
        data["llm_error"] = self.llm_error
        data["quality_note"] = self.quality_note
        return data


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _keyword(query: str) -> str:
    match = KEYWORD_RE.search(query)
    if not match:
        return ""
    return match.group(1).strip()[:180]


def _period(query: str) -> timedelta | None:
    hours = HOURS_RE.search(query)
    if hours:
        return timedelta(hours=int(hours.group(1)))
    days = DAYS_RE.search(query)
    if days:
        return timedelta(days=int(days.group(1)))
    return None


def _count(query: str) -> int | None:
    for pattern in COUNT_RES:
        match = pattern.search(query.lower())
        if match:
            return int(match.group(1))
    return None


def _fields(query: str) -> list[str]:
    found = [name for name in ALLOWED_FIELDS if re.search(rf"\b{name}\b", query, re.I)]
    return found or list(ALLOWED_FIELDS)


def _review(reason: str, hints: list[str], steps: list[str]) -> Plan:
    return Plan(
        plan_steps=steps[:5],
        api_url="",
        fields_to_keep=[],
        confidence="low",
        needs_review=True,
        reason=reason,
        hints=hints[:3],
    )


def decide(query: str) -> Plan:
    """Решение по запросу. Сбор отсюда не запускается."""
    text = (query or "").strip()
    lowered = text.lower()

    ambiguous_hints = [
        "Укажите тему в кавычках, например «ноутбук».",
        "Добавьте период: за последние 24 часа или 7 дней.",
        "Перечислите поля: title, date, source, url.",
    ]
    impossible_hints = [
        "Замените закрытые данные на публичные новости по открытой теме.",
        "Укажите ключевое слово в кавычках, например «рынок ноутбуков».",
        "Укажите период не длиннее 30 дней.",
    ]
    stop_steps = [
        "Разобрать формулировку запроса.",
        "Остановить автоматический сбор: уверенности недостаточно.",
        "Вернуть причину и не запускать сбор.",
    ]

    if not text:
        return _review("Запрос пустой: неясно, какие новости собирать.", ambiguous_hints, stop_steps)

    if any(marker in lowered for marker in IMPOSSIBLE_MARKERS):
        return _review(
            "Запрос невыполним: GNews отдаёт только публичные новости, а не закрытую статистику, приватные данные или сведения о конкурентах.",
            impossible_hints,
            stop_steps,
        )

    if any(marker in lowered for marker in AMBIGUOUS_MARKERS):
        return _review(
            "Запрос двусмысленный: неясно, какую тему собирать и по какому ограничению фильтровать.",
            ambiguous_hints,
            stop_steps,
        )

    if re.search(r"за\s+год", lowered) or re.search(r"\b365\b", lowered):
        return _review(
            "Запрос невыполним: источник хранит новости только за последние 30 дней.",
            impossible_hints,
            stop_steps,
        )

    keyword = _keyword(text)
    period = _period(lowered)
    count = _count(lowered)
    has_fields = bool(re.search(r"\b(title|date|source|url)\b", text, re.I))
    has_constraint = period is not None or count is not None or has_fields

    if not keyword or not has_constraint:
        return _review(
            "Запрос двусмысленный: нужна тема в кавычках и хотя бы одно ограничение — период, количество или список полей.",
            ambiguous_hints,
            stop_steps,
        )

    if period is not None and period > FREE_HISTORY:
        return _review(
            "Запрос невыполним: период длиннее 30 дней, источник хранит только последний месяц.",
            impossible_hints,
            stop_steps,
        )

    limit = FREE_MAX if count is None else max(1, min(count, FREE_MAX))
    fields = _fields(text)
    # GNews отдаёт материалы с задержкой около 12 часов.
    window_end = datetime.now(timezone.utc) - FREE_DELAY
    window_start = window_end - (period or timedelta(days=7))
    if window_end - window_start > FREE_HISTORY:
        window_start = window_end - FREE_HISTORY

    params = {
        "q": keyword,
        "lang": "ru",
        "max": str(limit),
        "sortby": "publishedAt",
        "in": "title,description",
        "from": _iso(window_start),
        "to": _iso(window_end),
    }
    capped = count is not None and count > FREE_MAX
    confidence = "medium" if capped or period is None else "high"
    steps = [
        f"Взять тему «{keyword}» и ограничение из запроса.",
        f"Запросить GNews: до {limit} материалов.",
        f"Оставить только поля {', '.join(fields)} и сохранить их в выбранный набор.",
    ]
    if capped:
        steps.insert(1, f"В запросе {count} записей. В выборку попадёт не больше {FREE_MAX}.")
    if period is None:
        steps.insert(1, "Период в запросе не указан: взять последние 7 дней внутри бесплатного окна.")

    return Plan(
        plan_steps=steps[:5],
        api_url=public_url(params),
        fields_to_keep=fields,
        confidence=confidence,
        needs_review=False,
        keyword=keyword,
        search_params=params,
        quality_note="Тема и ограничение указаны, сбор разрешён.",
    )


def _sanitize_steps(value) -> list[str]:
    if not isinstance(value, list):
        return []
    steps = [str(item).strip() for item in value if str(item).strip()]
    if not 1 <= len(steps) <= 5:
        return []
    return steps


def ask_model(query: str) -> dict:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return {"_error": "OPENAI_API_KEY пуст"}

    base = os.environ.get("OPENAI_BASE_URL", "https://api.proxyapi.ru/openai/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    try:
        temperature = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
    except ValueError:
        temperature = 0.2
    temperature = min(1.0, max(0.0, temperature))

    system = (
        "Ты планировщик сбора публичных новостей через GNews. "
        "Верни только один JSON-объект со ключами plan_steps (1–5 строк), "
        "api_url (без apikey), fields_to_keep (только title, date, source, url), "
        "confidence (high, medium или low), needs_review (boolean), reason (строка), hints (массив). "
        "Корректный запрос: ясна тема и есть хотя бы одно ограничение (время, количество или поля) — "
        "needs_review=false. Двусмысленный запрос без темы или с формулировками "
        "«самое важное», «сделай вывод», «по рынку» — needs_review=true и confidence=low. "
        "Невыполнимый запрос про закрытую статистику, приватные данные или конкурентов — "
        "needs_review=true и confidence=low. Новости не выдумывай. Ключ API не пиши."
    )
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": 600,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ],
    }

    def _post(body: dict) -> httpx.Response:
        return httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=40,
        )

    try:
        rich = dict(payload)
        rich["response_format"] = {"type": "json_object"}
        response = _post(rich)
        if response.status_code == 400:
            response = _post(payload)
        if response.status_code >= 400:
            return {"_error": f"модель ответила {response.status_code}"}
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return {"_error": "модель вернула не объект"}
        return parsed
    except httpx.HTTPError:
        return {"_error": "нет соединения с языковой моделью"}
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {"_error": "модель вернула не строгий JSON"}


def build_plan(query: str) -> Plan:
    plan = decide(query)
    if not (query or "").strip():
        return plan

    raw = ask_model(query)
    if raw.get("_error"):
        plan.llm_error = str(raw["_error"])
        plan.model_used = False
        plan.quality_note = (
            plan.quality_note
            or "Модель не ответила. Решение приняла проверка запроса."
        )
        return plan

    plan.llm_raw = raw
    plan.model_used = True
    model_review = bool(raw.get("needs_review"))
    if plan.needs_review:
        if not model_review:
            plan.quality_note = (
                "Проверка запроса остановила сбор: формулировка неясная или невыполнимая."
            )
        else:
            plan.quality_note = "Проверка запроса подтвердила остановку."
        return plan

    steps = _sanitize_steps(raw.get("plan_steps"))
    if steps:
        plan.plan_steps = steps
    if model_review or str(raw.get("confidence", "")).lower() == "low":
        plan.quality_note = (
            "Сбор разрешён: в запросе есть тема и ограничение."
        )
    else:
        plan.quality_note = "План подтверждён."
    return plan


def to_records(articles: list[dict], fields: list[str]) -> list[dict]:
    kept = [name for name in fields if name in ALLOWED_FIELDS] or list(ALLOWED_FIELDS)
    records = []
    for article in articles:
        source = article.get("source") or {}
        source_name = source.get("name") if isinstance(source, dict) else ""
        full = {
            "title": article.get("title") or "",
            "date": article.get("publishedAt") or "",
            "source": source_name or "",
            "url": article.get("url") or "",
        }
        records.append({name: full[name] for name in kept})
    return records
