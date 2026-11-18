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
