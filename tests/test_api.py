import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.client import ChatResult, FakeLLM, ToolCall
from app.main import create_app
from app.rag.ingest import ingest


@pytest.fixture
def settings(tmp_path):
    s = Settings(_env_file=None, llm_provider="fake", embed_provider="local",
                 index_dir=str(tmp_path / "index"), usage_log="", retrieval_min_score=0.1)
    ingest(s)
    return s


def client(settings, script):
    return TestClient(create_app(settings, llm=FakeLLM(script)))


def test_health(settings):
    with client(settings, []) as c:
        body = c.get("/health").json()
    assert body["status"] == "ok" and body["docs_index"] > 10 and body["odoo"] == "mock"


def test_ask_returns_sources(settings):
    with client(settings, ["Use the Barcode app [1]."]) as c:
        r = c.post("/ask", json={"question": "How do I move stock between locations?"})
    assert r.status_code == 200
    assert any(s["source"] == "05-barcode-pda-faq.md" for s in r.json()["sources"])


def test_agent_endpoint(settings):
    script = [ChatResult(None, [ToolCall("1", "get_stock_by_location", {"product": "mozzarella"})]),
              "It is in the cold room."]
    with client(settings, script) as c:
        r = c.post("/agent", json={"question": "Where is the mozzarella?"})
    assert r.json()["steps"][0]["tool"] == "get_stock_by_location"


def test_extract_from_text(settings):
    note = {"supplier_name": "Conservas Ebro SA", "supplier_vat": "A87654321",
            "document_number": "CE-55120", "lines": [
                {"product_code": "SAU-400", "description": "Tomato sauce", "quantity": 500,
                 "lot": "SAU-2611-D", "expiration_date": "2027-05-31"}]}
    with client(settings, [json.dumps(note)]) as c:
        r = c.post("/extract", data={"text": open("data/samples/albaran_conservas.txt").read()})
    body = r.json()
    assert body["supplier"]["name"] == "Conservas Ebro SA"
    assert body["ready_to_import"] is True


def test_validation_error(settings):
    with client(settings, []) as c:
        assert c.post("/agent", json={"question": ""}).status_code == 422


def test_api_token_required_when_configured(settings):
    s = settings.model_copy(update={"api_token": "secret"})
    with client(s, ["ok"]) as c:
        assert c.get("/health").status_code == 200
        assert c.post("/agent", json={"question": "hello"}).status_code == 401
        r = c.post("/agent", json={"question": "hello"}, headers={"X-API-Key": "secret"})
        assert r.status_code == 200
