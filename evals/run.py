"""Evaluation suites.

    python -m evals.run retrieval   # no LLM needed; works with EMBED_PROVIDER=local
    python -m evals.run agent       # needs a real LLM (uses the free-tier quota)
    python -m evals.run extraction  # needs a real LLM

Results are printed and saved to evals/results/<suite>-<timestamp>.json so changes in
prompts, models or chunking can be compared over time.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from app.config import get_settings

HERE = Path(__file__).parent


def _load(name: str) -> list[dict]:
    return json.loads((HERE / name).read_text())


def eval_retrieval(settings, k: int = 3) -> dict:
    from app.rag.chunking import load_documents
    from app.rag.embeddings import build_embedder
    from app.rag.store import NumpyStore

    embedder = build_embedder(settings)
    chunks = load_documents(settings.docs_dir)
    store = NumpyStore(embedder.name)
    store.add(chunks, embedder.embed([c.text for c in chunks]))

    cases, hits, rr, details = _load("retrieval_cases.json"), 0, 0.0, []
    for case in cases:
        results = store.search(embedder.embed([case["question"]])[0], k)
        sources = [c.source for c, _ in results]
        rank = sources.index(case["expected_source"]) + 1 if case["expected_source"] in sources else 0
        hits += rank > 0
        rr += 1 / rank if rank else 0
        details.append({"question": case["question"], "rank": rank, "got": sources})
    return {"embedder": embedder.name, "k": k, f"hit@{k}": round(hits / len(cases), 3),
            "mrr": round(rr / len(cases), 3), "cases": details}


def eval_agent(settings) -> dict:
    from app.agent.loop import run_agent
    from app.main import build_state

    state = build_state(settings)
    results, passed = [], 0
    for case in _load("agent_cases.json"):
        out = run_agent(case["question"], state["llm"], state["tools"])
        used = [s["tool"] for s in out["steps"]]
        tools_ok = all(t in used for t in case["expected_tools"])
        text = out["answer"].lower()
        content_ok = (all(s.lower() in text for s in case["must_contain"])
                      and not any(s.lower() in text for s in case.get("must_not_contain", [])))
        passed += tools_ok and content_ok
        results.append({"question": case["question"], "tools_ok": tools_ok,
                        "content_ok": content_ok, "tools_used": used,
                        "ms": out["elapsed_ms"], "answer": out["answer"][:400]})
        time.sleep(4)  # stay under free-tier requests-per-minute limits
    return {"model": state["llm"].model, "pass_rate": round(passed / len(results), 3),
            "usage": state["tracker"].summary(), "cases": results}


def eval_extraction(settings) -> dict:
    from app.extract.extractor import extract_delivery_note
    from app.main import build_state

    state = build_state(settings)
    fields_total = fields_ok = 0
    results = []
    for case in _load("extraction_cases.json"):
        exp = case["expected"]
        note = extract_delivery_note(Path(case["file"]).read_text(), state["llm"])
        got = note.model_dump(mode="json")
        checks = {k: got.get(k) == exp[k]
                  for k in ("supplier_vat", "document_number", "document_date", "purchase_order")}
        checks["n_lines"] = len(got["lines"]) == exp["n_lines"]
        for i, line in enumerate(exp["lines"]):
            g = got["lines"][i] if i < len(got["lines"]) else {}
            for key, val in line.items():
                checks[f"line{i + 1}.{key}"] = g.get(key) == val
        fields_total += len(checks)
        fields_ok += sum(checks.values())
        results.append({"file": case["file"],
                        "failed": [k for k, ok in checks.items() if not ok]})
        time.sleep(4)
    return {"model": state["llm"].model, "field_accuracy": round(fields_ok / fields_total, 3),
            "usage": state["tracker"].summary(), "cases": results}


SUITES = {"retrieval": eval_retrieval, "agent": eval_agent, "extraction": eval_extraction}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", choices=SUITES)
    parser.add_argument("--min", type=float, default=None,
                        help="fail (exit 1) if the main metric is below this value")
    args = parser.parse_args()

    report = SUITES[args.suite](get_settings())
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{args.suite}-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False))

    summary = {k: v for k, v in report.items() if k != "cases"}
    print(json.dumps(summary, indent=2))
    for c in report["cases"]:
        bad = c.get("rank") == 0 or c.get("failed") or c.get("tools_ok") is False \
            or c.get("content_ok") is False
        if bad:
            print("FAIL:", json.dumps(c, ensure_ascii=False)[:300])

    metric = next(v for k, v in report.items() if k.startswith(("hit@", "pass_rate", "field_")))
    if args.min is not None and metric < args.min:
        sys.exit(f"Metric {metric} below threshold {args.min}")
