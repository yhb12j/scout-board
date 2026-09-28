import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_PATH"] = str(Path(__file__).resolve().parent / "_tmp" / "test.db")
os.environ["OPENAI_API_KEY"] = ""
os.environ["GNEWS_API_KEY"] = "test-key-not-real"
os.environ["LLM_TEMPERATURE"] = "0.2"

from app import db
from app.gnews_client import GNewsError
from app.main import app

QUERIES = Path(__file__).resolve().parents[1] / "tests_data" / "queries.jsonl"
VALID_QUERY = "Собери новости за последние 24 часа по запросу «ноутбук» и сохрани поля title, date, source, url"


@pytest.fixture()
def client():
    Path(os.environ["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    db.init_db()
    with db.connect() as conn:
        conn.executescript(
            """
            DELETE FROM records;
            DELETE FROM datasets;
            DELETE FROM agent_runs;
            DELETE FROM audit_runs;
            """
        )
    with TestClient(app) as test_client:
        yield test_client


def test_home_and_health(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Наборы" in page.text
    assert "Витрина" in page.text
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["gnews_configured"] is True
    assert health["llm_configured"] is False


def test_empty_dataset_is_rejected_and_audited(client):
    response = client.post("/datasets", json={"name": "  ", "source": "GNews"})
    assert response.status_code == 400
    audits = client.get("/audit/runs").json()
    assert audits[0]["action"] == "create_dataset"
    assert audits[0]["status"] == "error"


def test_create_dataset_returns_id(client):
    response = client.post("/datasets", json={"name": "Новости недели", "source": "GNews"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dataset_id"]
    listed = client.get("/datasets").json()
    assert listed[0]["name"] == "Новости недели"
    assert listed[0]["source"] == "GNews"
    assert listed[0]["dataset_id"] == body["dataset_id"]


def test_ten_queries_match_expected_review(client):
    rows = [json.loads(line) for line in QUERIES.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 10
    assert sum(1 for row in rows if row["expected_needs_review"] is False) == 7
    assert sum(1 for row in rows if row["expected_needs_review"] is True) == 3
    for row in rows:
        response = client.post("/ai/plan_and_collect", json={"query": row["query"]})
        assert response.status_code == 200, row["query"]
        body = response.json()
        assert body["needs_review"] is row["expected_needs_review"]
        assert body["confidence"] in {"high", "medium", "low"}
        assert 1 <= len(body["plan_steps"]) <= 5
        assert "apikey" not in json.dumps(body, ensure_ascii=False).lower()
        if body["needs_review"]:
            assert body["confidence"] == "low"
            assert body["reason"]
            assert len(body["hints"]) >= 2
            assert body["api_url"] == ""
        else:
            assert body["confidence"] in {"high", "medium"}
            assert body["api_url"].startswith("https://gnews.io/api/v4/search?")
            assert body["fields_to_keep"] == ["title", "date", "source", "url"]
    agents = client.get("/agent_runs?limit=20").json()
    audits = client.get("/audit/runs?limit=20").json()
    assert len(agents) >= 10
    assert len(audits) >= 10
    reviewed = [item for item in agents if item["needs_review"]]
    assert reviewed
    assert all(item["error"] for item in reviewed)


def test_quality_gate_overrides_confident_model(client, monkeypatch):
    monkeypatch.setattr(
        "app.planner.ask_model",
        lambda query: {
            "plan_steps": ["Собрать всё без проверки."],
            "needs_review": False,
            "confidence": "high",
        },
    )
    response = client.post("/ai/plan_and_collect", json={"query": "Собери самое важное"})
    body = response.json()
    assert body["needs_review"] is True
    assert body["confidence"] == "low"
    assert body["model_used"] is True
    assert "Собрать всё" not in " ".join(body["plan_steps"])


def test_ambiguous_collect_does_not_call_gnews(client, monkeypatch):
    def fail_if_called(_params):
        raise AssertionError("GNews нельзя вызывать для ручной проверки")

    monkeypatch.setattr("app.gnews_client.search", fail_if_called)
    created = client.post("/datasets", json={"name": "demo", "source": "GNews"}).json()
    response = client.post(
        f"/datasets/{created['dataset_id']}/collect",
        json={"query": "Собери самое важное"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "needs_review"
    assert body["records_saved"] == 0
    assert body["reason"]


def test_collect_saves_only_kept_fields(client, monkeypatch):
    monkeypatch.setattr(
            "app.gnews_client.search",
            lambda _params: [
                {
                    "title": "Ноутбук подешевел",
                "description": "это описание не сохраняем",
                "content": "полный текст не сохраняем",
                "url": "https://example.com/news",
                "publishedAt": "2026-09-27T10:00:00Z",
                "source": {"name": "Example News"},
            }
        ],
    )
    created = client.post("/datasets", json={"name": "demo", "source": "GNews"}).json()
    response = client.post(
        f"/datasets/{created['dataset_id']}/collect",
        json={"query": VALID_QUERY},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "records_saved": 1}
    records = client.get(f"/datasets/{created['dataset_id']}/records?limit=50").json()
    assert records[0]["created_at"]
    assert records[0]["dataset_id"] == created["dataset_id"]
    assert records[0]["record_json"] == {
        "title": "Ноутбук подешевел",
        "date": "2026-09-27T10:00:00Z",
        "source": "Example News",
        "url": "https://example.com/news",
    }


def test_missing_dataset_is_404(client):
    response = client.post("/datasets/missing/collect", json={"query": VALID_QUERY})
    assert response.status_code == 404


def test_gnews_failure_is_audited(client, monkeypatch):
    def explode(_params):
        raise GNewsError("сервис недоступен", status_code=502)

    monkeypatch.setattr("app.gnews_client.search", explode)
    created = client.post("/datasets", json={"name": "demo", "source": "GNews"}).json()
    response = client.post(
        f"/datasets/{created['dataset_id']}/collect",
        json={"query": VALID_QUERY},
    )
    assert response.status_code == 502
    audits = client.get("/audit/runs").json()
    collect_audit = next(item for item in audits if item["action"] == "collect")
    assert collect_audit["status"] == "error"
    assert "сервис недоступен" in collect_audit["error"]
    assert "apikey" not in json.dumps(collect_audit, ensure_ascii=False).lower()


def test_missing_gnews_key_is_audited(client, monkeypatch):
    monkeypatch.setenv("GNEWS_API_KEY", "")
    created = client.post("/datasets", json={"name": "demo", "source": "GNews"}).json()
    response = client.post(
        f"/datasets/{created['dataset_id']}/collect",
        json={"query": VALID_QUERY},
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "Источник новостей не настроен."
    audits = client.get("/audit/runs").json()
    assert any(item["action"] == "collect" and item["status"] == "error" for item in audits)


def test_export_json_and_csv(client, monkeypatch):
    monkeypatch.setattr(
            "app.gnews_client.search",
            lambda _params: [
                {
                    "title": "Доставка",
                "url": "https://example.com/a",
                "publishedAt": "2026-09-27T10:00:00Z",
                "source": {"name": "Новости"},
            }
        ],
    )
    created = client.post("/datasets", json={"name": "demo", "source": "GNews"}).json()
    client.post(f"/datasets/{created['dataset_id']}/collect", json={"query": VALID_QUERY})
    exported = client.get(f"/datasets/{created['dataset_id']}/export?format=json")
    assert exported.status_code == 200
    assert exported.json()[0]["record_json"]["title"] == "Доставка"
    csv_response = client.get(f"/datasets/{created['dataset_id']}/export?format=csv")
    assert csv_response.status_code == 200
    assert "title" in csv_response.text
    assert "Доставка" in csv_response.text
