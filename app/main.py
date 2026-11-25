import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.agent.loop import run_agent
from app.agent.sessions import SessionStore
from app.agent.tools import build_tools
from app.config import Settings, get_settings
from app.extract.extractor import (
    ExtractionError,
    extract_delivery_note,
    match_with_odoo,
    pdf_to_text,
)
from app.llm.client import FakeLLM, LLMError, OpenAICompatLLM
from app.llm.usage import UsageTracker
from app.odoo.client import build_odoo
from app.rag.embeddings import build_embedder
from app.rag.pipeline import Retriever, answer_question
from app.rag.store import NumpyStore, PgVectorStore, download_index_from_s3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")


def _api_key(settings: Settings) -> str:
    if settings.llm_api_key or not settings.llm_api_key_ssm_param:
        return settings.llm_api_key
    import boto3

    param = boto3.client("ssm").get_parameter(Name=settings.llm_api_key_ssm_param,
                                              WithDecryption=True)
    return param["Parameter"]["Value"]


def build_retriever(settings: Settings, api_key: str) -> Retriever | None:
    """Load the documentation index; None if it is not available (the rest still works)."""
    try:
        embedder = build_embedder(settings.model_copy(update={"llm_api_key": api_key}))
        if settings.vector_store == "pgvector":
            store = PgVectorStore(settings.database_url, embedder.name,
                                  embedder.embed(["dim probe"]).shape[1])
        else:
            if settings.s3_bucket and not Path(settings.index_dir, "meta.json").exists():
                download_index_from_s3(settings.s3_bucket, settings.s3_index_prefix,
                                       settings.index_dir)
            store = NumpyStore.load(settings.index_dir)
        log.info("Index loaded: %d chunks (%s)", store.count(), embedder.name)
        return Retriever(embedder, store, settings.retrieval_k, settings.retrieval_min_score)
    except Exception as e:
        log.warning("Documentation index not available (%s). /ask is disabled.", e)
        return None


def build_state(settings: Settings, llm=None) -> dict:
    tracker = UsageTracker(settings.usage_log)
    key = _api_key(settings)
    if llm is None:
        if settings.llm_provider == "fake":
            llm = FakeLLM()
        else:
            llm = OpenAICompatLLM(settings.chat_base_url, key, settings.chat_model, tracker,
                                  settings.llm_temperature,
                                  fallback_models=settings.chat_fallback_models)

    retriever = build_retriever(settings, key)
    odoo = build_odoo(settings)
    return {"llm": llm, "tracker": tracker, "retriever": retriever, "odoo": odoo,
            "tools": build_tools(odoo, retriever), "sessions": SessionStore(),
            "settings": settings}


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.s = build_state(settings, llm)
        yield

    app = FastAPI(title="Odoo AI Assistant", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def check_token(request: Request, call_next):
        # Public deployments: without this anyone could spend the LLM quota.
        if settings.api_token and request.url.path not in ("/", "/health"):
            given = request.headers.get("x-api-key") or request.query_params.get("key") or ""
            if not secrets.compare_digest(given, settings.api_token):
                return JSONResponse({"detail": "Missing or invalid API key"}, status_code=401)
        return await call_next(request)

    class Question(BaseModel):
        question: str = Field(min_length=3, max_length=1000)

    class AgentQuestion(Question):
        session_id: str | None = Field(
            None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
            description="Send back the session_id of a previous answer to ask a follow-up "
                        "question. Omit it to start a new conversation.")

    @app.get("/", include_in_schema=False)
    def ui():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/health")
    def health():
        s = app.state.s
        return {"status": "ok", "llm": f"{settings.llm_provider}:{getattr(s['llm'], 'model', '')}",
                "docs_index": s["retriever"].store.count() if s["retriever"] else 0,
                "odoo": settings.odoo_mode}

    @app.post("/ask")
    def ask(q: Question):
        s = app.state.s
        if not s["retriever"]:
            raise HTTPException(503, "Documentation index not built. Run python -m app.rag.ingest")
        try:
            return answer_question(q.question, s["retriever"], s["llm"])
        except LLMError as e:
            raise HTTPException(503, str(e)) from e

    @app.post("/agent")
    def agent(q: AgentQuestion):
        s = app.state.s
        sessions: SessionStore = s["sessions"]
        session_id = q.session_id or sessions.new_id()
        try:
            result = run_agent(q.question, s["llm"], s["tools"],
                               history=sessions.history(session_id))
        except LLMError as e:
            raise HTTPException(503, str(e)) from e
        sessions.append(session_id, q.question, result["answer"])
        return {**result, "session_id": session_id}

    @app.delete("/agent/sessions/{session_id}", status_code=204)
    def end_session(session_id: str):
        app.state.s["sessions"].clear(session_id)

    @app.post("/extract")
    async def extract(text: str | None = Form(None), file: UploadFile | None = File(None)):
        s = app.state.s
        if file is not None:
            data = await file.read()
            if len(data) > 5_000_000:
                raise HTTPException(413, "File too large (max 5 MB)")
            content = pdf_to_text(data) if file.filename.lower().endswith(".pdf") else data.decode()
        else:
            content = text or ""
        if len(content.strip()) < 20:
            raise HTTPException(422, "Send the delivery note as `text` or as a PDF/TXT `file`")
        try:
            note = extract_delivery_note(content[:15000], s["llm"])
        except ExtractionError as e:
            raise HTTPException(422, str(e)) from e
        except LLMError as e:
            raise HTTPException(503, str(e)) from e
        return match_with_odoo(note, s["odoo"])

    @app.get("/usage")
    def usage():
        return app.state.s["tracker"].summary()

    return app


app = create_app()
