"""Поиск новостей через GNews."""

import os
from urllib.parse import urlencode

import httpx

SEARCH_URL = "https://gnews.io/api/v4/search"
FREE_MAX = 10


class GNewsError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _safe_error(response: httpx.Response) -> str:
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            errors = body.get("errors") or body.get("message") or body.get("error")
            if isinstance(errors, list):
                detail = "; ".join(str(item) for item in errors[:3])
            elif errors:
                detail = str(errors)
    except Exception:
        detail = ""
    detail = detail.replace(os.environ.get("GNEWS_API_KEY", ""), "***")
    if len(detail) > 300:
        detail = detail[:300]
    suffix = f": {detail}" if detail else ""
    return f"GNews ответил кодом {response.status_code}{suffix}"


def search(params: dict) -> list[dict]:
    """params без apikey. Ключ добавляется только в исходящий запрос."""
    key = os.environ.get("GNEWS_API_KEY", "").strip()
    if not key:
        raise GNewsError(
            "Источник новостей не настроен.",
            status_code=500,
        )

    outgoing = dict(params)
    outgoing["max"] = str(min(int(outgoing.get("max", FREE_MAX)), FREE_MAX))
    outgoing["apikey"] = key

    articles = _request(outgoing)
    if not articles and outgoing.get("lang"):
        retry = dict(outgoing)
        retry.pop("lang", None)
        articles = _request(retry)
    return articles


def _request(params: dict) -> list[dict]:
    try:
        response = httpx.get(SEARCH_URL, params=params, timeout=30)
    except httpx.HTTPError as exc:
        raise GNewsError(
            f"Нет соединения с GNews ({exc.__class__.__name__}).",
            status_code=502,
        ) from exc

    if response.status_code >= 400:
        code = 500 if response.status_code == 401 else 502
        raise GNewsError(_safe_error(response), status_code=code)

    try:
        body = response.json()
    except Exception as exc:
        raise GNewsError("GNews вернул не JSON.", status_code=502) from exc

    articles = body.get("articles")
    if not isinstance(articles, list):
        raise GNewsError("В ответе GNews нет списка articles.", status_code=502)
    return articles


def public_url(params: dict) -> str:
    """Адрес для экрана и аудита: без ключа."""
    visible = {key: value for key, value in params.items() if key != "apikey"}
    return f"{SEARCH_URL}?{urlencode(visible)}"
