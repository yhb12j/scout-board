"""Веб-панель и API Scout Board."""

import csv
import io
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db, gnews_client
from app.gnews_client import GNewsError
from app.planner import build_plan, decide, to_records

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    gnews = "задан" if os.environ.get("GNEWS_API_KEY", "").strip() else "не задан"
    llm = "задан" if os.environ.get("OPENAI_API_KEY", "").strip() else "ПУСТО"
    print("Scout Board: http://127.0.0.1:8000")
    print(f"Ключ GNews: {gnews}")
    print(f"Ключ языковой модели: {llm}")
    yield


app = FastAPI(title="Scout Board", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


class DatasetIn(BaseModel):
    name: str = ""
    source: str = ""


class QueryIn(BaseModel):
    query: str = Field(default="", max_length=1000)


def _text(value: str, field: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"Поле {field} не должно быть пустым")
    if len(cleaned) > 200:
        raise HTTPException(status_code=400, detail=f"Поле {field} длиннее 200 символов")
    return cleaned


def _audit(action: str, payload, output, status: str, error: str | None, started: float) -> None:
    db.write_audit(action, payload, output, status, error, started)


@app.get("/")
def index():
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "gnews_configured": bool(os.environ.get("GNEWS_API_KEY", "").strip()),
        "llm_configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
    }


@app.post("/datasets")
def create_dataset(body: DatasetIn):
    started = time.perf_counter()
    payload = body.model_dump()
    try:
        name = _text(body.name, "name")
        source = _text(body.source, "source")
        dataset_id = db.insert_dataset(name, source)
        result = {"status": "ok", "dataset_id": dataset_id}
        _audit("create_dataset", {"name": name, "source": source}, result, "ok", None, started)
        return result
    except HTTPException as exc:
        _audit("create_dataset", payload, {"detail": exc.detail}, "error", str(exc.detail), started)
        raise


@app.get("/datasets")
def get_datasets():
    return db.list_datasets()


@app.post("/ai/plan_and_collect")
def plan_and_collect(body: QueryIn):
    started = time.perf_counter()
    query = body.query.strip()
    plan = build_plan(query)
    public = plan.public()
    error = plan.reason if plan.needs_review else None
    db.insert_agent_run(query, plan.stored(), plan.needs_review, error)
    _audit("plan_and_collect", {"query": query}, public, "needs_review" if plan.needs_review else "ok", error, started)
    return public


@app.post("/datasets/{dataset_id}/collect")
def collect(dataset_id: str, body: QueryIn):
    started = time.perf_counter()
    query = body.query.strip()
    payload = {"dataset_id": dataset_id, "query": query}
    dataset = db.get_dataset(dataset_id)
    if dataset is None:
        detail = "Набор данных не найден"
        _audit("collect", payload, {"detail": detail}, "error", detail, started)
        raise HTTPException(status_code=404, detail=detail)

    plan = decide(query)
    error = plan.reason if plan.needs_review else None
    db.insert_agent_run(query, plan.stored(), plan.needs_review, error)
    if plan.needs_review:
        result = {
            "status": "needs_review",
            "records_saved": 0,
            "reason": plan.reason,
            "hints": plan.hints,
        }
        _audit("collect", payload, result, "needs_review", plan.reason, started)
        return JSONResponse(status_code=422, content=result)

    try:
        articles = gnews_client.search(plan.search_params)
        records = to_records(articles, plan.fields_to_keep)
        saved = db.insert_records(dataset_id, records)
        result = {"status": "ok", "records_saved": saved}
        _audit("collect", payload, result, "ok", None, started)
        return result
    except GNewsError as exc:
        result = {"status": "error", "records_saved": 0, "detail": exc.message}
        _audit("collect", payload, result, "error", exc.message, started)
        return JSONResponse(status_code=exc.status_code, content=result)
    except Exception:
        detail = "Внутренняя ошибка сбора. Новости не сохранены."
        result = {"status": "error", "records_saved": 0, "detail": detail}
        _audit("collect", payload, result, "error", detail, started)
        return JSONResponse(status_code=500, content=result)


@app.get("/datasets/{dataset_id}/records")
def get_records(dataset_id: str, limit: int = Query(default=50)):
    started = time.perf_counter()
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="Параметр limit должен быть от 1 до 500")
    dataset = db.get_dataset(dataset_id)
    if dataset is None:
        detail = "Набор данных не найден"
        _audit("list_records", {"dataset_id": dataset_id, "limit": limit}, {"detail": detail}, "error", detail, started)
        raise HTTPException(status_code=404, detail=detail)
    rows = db.list_records(dataset_id, limit)
    _audit(
        "list_records",
        {"dataset_id": dataset_id, "limit": limit},
        {"count": len(rows)},
        "ok",
        None,
        started,
    )
    return rows


@app.get("/datasets/{dataset_id}/export")
def export_records(dataset_id: str, format: str = Query(default="json"), limit: int = Query(default=500)):
    started = time.perf_counter()
    if format not in {"json", "csv"}:
        raise HTTPException(status_code=400, detail="Формат должен быть json или csv")
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="Параметр limit должен быть от 1 до 500")
    dataset = db.get_dataset(dataset_id)
    payload = {"dataset_id": dataset_id, "format": format, "limit": limit}
    if dataset is None:
        detail = "Набор данных не найден"
        _audit("export", payload, {"detail": detail}, "error", detail, started)
        raise HTTPException(status_code=404, detail=detail)

    rows = db.list_records(dataset_id, limit)
    _audit("export", payload, {"count": len(rows), "format": format}, "ok", None, started)
    filename = f"records.{format}"
    if format == "json":
        return JSONResponse(
            content=rows,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return Response(
        content=_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv(rows: list[dict]) -> str:
    fieldnames = ["id", "created_at", "dataset_id"]
    extra = []
    for row in rows:
        for key in row.get("record_json") or {}:
            if key not in extra:
                extra.append(key)
    fieldnames.extend(extra)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        flat = {
            "id": row["id"],
            "created_at": row["created_at"],
            "dataset_id": row["dataset_id"],
        }
        flat.update(row.get("record_json") or {})
        writer.writerow(flat)
    return "\ufeff" + buffer.getvalue()


@app.get("/audit/runs")
def audit_runs(limit: int = Query(default=100)):
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="Параметр limit должен быть от 1 до 500")
    return db.list_audit(limit)


@app.get("/agent_runs")
def agent_runs(limit: int = Query(default=50)):
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="Параметр limit должен быть от 1 до 500")
    return db.list_agent_runs(limit)
