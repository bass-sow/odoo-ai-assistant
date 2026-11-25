import json

import numpy as np
import pytest

from app.agent.loop import run_agent
from app.agent.tools import build_tools
from app.extract.extractor import ExtractionError, extract_delivery_note, match_with_odoo
from app.llm.client import ChatResult, FakeLLM, ToolCall
from app.odoo.mock_data import build_mock
from app.rag.chunking import chunk_markdown, load_documents
from app.rag.embeddings import HashEmbedder
from app.rag.pipeline import NO_ANSWER, Retriever, answer_question
from app.rag.store import NumpyStore


@pytest.fixture(scope="module")
def retriever():
    emb = HashEmbedder()
    chunks = load_documents("data/docs")
    store = NumpyStore(emb.name)
    store.add(chunks, emb.embed([c.text for c in chunks]))
    return Retriever(emb, store, k=3, min_score=0.1)


@pytest.fixture
def odoo():
    return build_mock()


# --- RAG -------------------------------------------------------------------------------

def test_chunks_keep_heading_context():
    md = "# Receiving\n\nIntro.\n\n## Scanning\n\nScan the GS1 label.\n"
    chunks = chunk_markdown(md, "r.md")
    assert [c.title for c in chunks] == ["Receiving", "Receiving > Scanning"]
    assert chunks[1].text.startswith("Receiving > Scanning")


def test_long_section_is_split_with_overlap():
    body = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(10))
    chunks = chunk_markdown(f"# Doc\n\n{body}", "d.md", max_chars=500, overlap=50)
    assert len(chunks) > 1
    assert all(len(c.text) < 600 for c in chunks)


def test_numpy_store_roundtrip(tmp_path, retriever):
    retriever.store.save(str(tmp_path))
    loaded = NumpyStore.load(str(tmp_path))
    assert loaded.count() == retriever.store.count()
    v = retriever.embedder.embed(["FEFO picking"])[0]
    assert loaded.search(v, 1)[0][0].id == retriever.store.search(v, 1)[0][0].id


def test_retriever_rejects_index_from_other_embedder(retriever):
    with pytest.raises(ValueError, match="Run `python -m app.rag.ingest`"):
        Retriever(HashEmbedder(dim=64), retriever.store)


def test_retrieval_finds_the_right_document(retriever):
    hits = retriever.retrieve("How do I scrap an expired lot in Odoo?")
    assert "03-expired-stock.md" in [h.chunk.source for h in hits]


def test_answer_cites_sources(retriever):
    llm = FakeLLM(["Move it to quarantine and create a scrap order [1]."])
    out = answer_question("What do I do with an expired lot?", retriever, llm)
    assert out["grounded"] and out["sources"]
    prompt = llm.calls[0]["messages"][1]["content"]
    assert "[1]" in prompt and "Question: What do I do with an expired lot?" in prompt


def test_no_llm_call_when_nothing_relevant(retriever):
    strict = Retriever(retriever.embedder, retriever.store, min_score=0.99)
    llm = FakeLLM()
    out = answer_question("best paella recipe", strict, llm)
    assert out["answer"] == NO_ANSWER and llm.calls == []


def test_hash_embedder_is_normalised_and_deterministic():
    e = HashEmbedder()
    a, b = e.embed(["expired yogurt lot"]), e.embed(["expired yogurt lot"])
    assert np.allclose(a, b) and np.isclose(np.linalg.norm(a), 1.0)


# --- Odoo mock and tools ---------------------------------------------------------------

def test_mock_domain_with_many2one_and_dotted_path(odoo):
    rows = odoo.search_read("stock.quant", [("product_id", "ilike", "mozzarella"),
                                            ("location_id.usage", "=", "internal")],
                            ["lot_id", "quantity"])
    assert {r["lot_id"][1] for r in rows} == {"MOZ-2611-C", "MOZ-2612-A", "MOZ-2610-Z"}


def test_expiring_lots_tool(odoo):
    tools = build_tools(odoo)
    lots = tools["get_expiring_lots"].fn(days=7)
    names = [lot["lot"] for lot in lots]
    assert names[0] == "MOZ-2610-Z"  # most expired first
    assert {"YOG-2611-A", "YOG-2611-B", "MOZ-2611-C"} <= set(names)
    assert "YOG-2612-A" not in names
    assert next(x for x in lots if x["lot"] == "YOG-2611-A")["status"] == "expired"


def test_pending_deliveries_excludes_done_and_incoming(odoo):
    refs = [d["reference"] for d in build_tools(odoo)["get_pending_deliveries"].fn()]
    assert "WH/OUT/00409" not in refs and "WH/IN/00157" not in refs
    assert "WH/OUT/00412" in refs


def test_overdue_invoices_tool(odoo):
    tool = build_tools(odoo)["get_overdue_invoices"]
    result = tool.fn()
    names = [i["invoice"] for i in result["invoices"]]
    # Oldest first; paid and not yet due invoices are left out.
    assert names == ["INV/2026/00231", "INV/2026/00250", "INV/2026/00244", "INV/2026/00262"]
    assert result["invoices"][0]["days_overdue"] == 41
    assert result["total_due_by_customer"]["Restaurante La Plaça"] == 2460.5
    partial = next(i for i in result["invoices"] if i["invoice"] == "INV/2026/00250")
    assert partial["partially_paid"] and partial["amount_due"] == 1155.0


def test_overdue_invoices_min_days_and_customer(odoo):
    fn = build_tools(odoo)["get_overdue_invoices"].fn
    assert [i["invoice"] for i in fn(min_days=15)["invoices"]] == ["INV/2026/00231", "INV/2026/00250"]
    assert [i["invoice"] for i in fn(min_days=41)["invoices"]] == ["INV/2026/00231"]
    assert [i["invoice"] for i in fn(customer="mar blau")["invoices"]] == ["INV/2026/00250"]


def test_tool_schema_is_openai_format(odoo):
    schema = build_tools(odoo)["get_expiring_lots"].schema()
    assert schema["type"] == "function"
    assert schema["function"]["parameters"]["properties"]["days"]["type"] == "integer"


# --- Agent loop ------------------------------------------------------------------------

def test_agent_runs_tool_then_answers(odoo):
    llm = FakeLLM([
        ChatResult(None, [ToolCall("c1", "get_expiring_lots", {"days": 7})]),
        ChatResult("YOG-2611-B expires in 4 days."),
    ])
    out = run_agent("What expires this week?", llm, build_tools(odoo))
    assert out["answer"] == "YOG-2611-B expires in 4 days."
    assert out["steps"][0]["tool"] == "get_expiring_lots"
    tool_msg = llm.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "YOG-2611-B" in tool_msg["content"]


def test_agent_reports_invalid_arguments_to_the_model(odoo):
    llm = FakeLLM([
        ChatResult(None, [ToolCall("c1", "get_expiring_lots", {"days": "a lot"})]),
        ChatResult("Sorry."),
    ])
    run_agent("x", llm, build_tools(odoo))
    error = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert error["error"] == "invalid arguments"


def test_agent_stops_after_max_steps(odoo):
    loop_call = ChatResult(None, [ToolCall("c", "search_products", {"query": "milk"})])
    llm = FakeLLM([loop_call] * 3 + ["Final."])
    out = run_agent("x", llm, build_tools(odoo), max_steps=3)
    assert out["truncated"] and out["answer"] == "Final." and len(out["steps"]) == 3


# --- Extraction ------------------------------------------------------------------------

NOTE = {
    "supplier_name": "Lácteos del Vallès SL", "supplier_vat": "B-12345678",
    "document_number": "AV-2026-1187", "document_date": "2026-11-17", "purchase_order": "P00102",
    "lines": [
        {"product_code": "YOG-500", "description": "Yogur griego", "quantity": 240,
         "lot": "YOG-2612-C", "expiration_date": "2026-12-10"},
        {"product_code": "QF-200", "description": "Queso fresco", "quantity": 60,
         "lot": "QF-2611-A", "expiration_date": "2026-11-28"},
    ],
}


def test_extraction_retries_with_validation_errors():
    bad = {**NOTE, "lines": []}
    llm = FakeLLM([json.dumps(bad), json.dumps(NOTE)])
    note = extract_delivery_note("albarán...", llm)
    assert note.supplier_vat == "B12345678"
    assert "invalid" in llm.calls[1]["messages"][-1]["content"]


def test_extraction_gives_up():
    llm = FakeLLM(["not json", "still not json"])
    with pytest.raises(ExtractionError):
        extract_delivery_note("albarán...", llm)


def test_match_flags_unknown_products(odoo):
    llm = FakeLLM([json.dumps(NOTE)])
    result = match_with_odoo(extract_delivery_note("x", llm), odoo)
    assert result["supplier"]["id"] == 41  # VAT on the note has no ES prefix
    assert result["lines"][0]["odoo_product"]["name"] == "Greek Yogurt 500g"
    assert not result["ready_to_import"]
    assert any("QF-200" in issue for issue in result["issues"])


def test_llm_falls_back_when_model_is_unavailable(monkeypatch):
    from types import SimpleNamespace

    from app.llm.client import LLMError, OpenAICompatLLM

    llm = OpenAICompatLLM("http://x", "k", "busy-model", fallback_models=["spare-model"])
    tried = []

    def fake_create(model, kwargs, *errors):
        tried.append(model)
        if model == "busy-model":
            err = LLMError("503")
            err.unavailable = True
            raise err
        msg = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None), 5

    monkeypatch.setattr(llm, "_create", fake_create)
    assert llm.chat([{"role": "user", "content": "hi"}]).content == "ok"
    assert tried == ["busy-model", "spare-model"]
    assert llm.model == "spare-model"  # sticks with the model that answered


# --- Conversation memory -----------------------------------------------------------------

def test_session_store_keeps_last_turns():
    from app.agent.sessions import SessionStore

    store = SessionStore(max_turns=2)
    for i in range(3):
        store.append("s1", f"q{i}", f"a{i}")
    history = store.history("s1")
    assert [m["content"] for m in history] == ["q1", "a1", "q2", "a2"]
    assert store.history("other") == []


def test_session_store_expires_and_evicts(monkeypatch):
    from app.agent import sessions

    now = [1000.0]
    monkeypatch.setattr(sessions.time, "monotonic", lambda: now[0])
    store = sessions.SessionStore(ttl_seconds=60, max_sessions=2)
    store.append("a", "q", "a")
    now[0] += 61
    assert store.history("a") == []
    for sid in ("b", "c", "d"):
        store.append(sid, "q", "a")
    assert store.history("b") == [] and store.history("d")


def test_agent_sends_history_to_the_model(odoo):
    llm = FakeLLM(["Second answer"])
    history = [{"role": "user", "content": "Where is the mozzarella?"},
               {"role": "assistant", "content": "In the cold room."}]
    run_agent("And how many units?", llm, build_tools(odoo), history=history)
    sent = llm.calls[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[-1]["content"] == "And how many units?"


# --- MCP server ------------------------------------------------------------------------

def test_mcp_server_exposes_the_agent_tools(odoo):
    import anyio
    from mcp import Client

    from app.mcp_server import build_server

    tools = build_tools(odoo)
    server = build_server(tools=tools)

    async def scenario():
        async with Client(server) as client:
            listed = {t.name: t for t in (await client.list_tools()).tools}
            ok = await client.call_tool("get_stock_by_location", {"product": "mozzarella"})
            bad = await client.call_tool("get_expiring_lots", {"days": 999})
            return listed, ok, bad

    listed, ok, bad = anyio.run(scenario)
    assert set(listed) == set(tools)
    expiring = listed["get_expiring_lots"]
    assert expiring.annotations.read_only_hint is True
    days = expiring.input_schema["properties"]["days"]
    assert days["maximum"] == 365 and "Window in days" in days["description"]
    assert "product" in listed["get_stock_by_location"].input_schema["required"]

    assert not ok.is_error
    rows = json.loads(ok.content[0].text)
    assert {r["location"] for r in rows} >= {"WH/Stock/Cold Room"}
    assert bad.is_error
