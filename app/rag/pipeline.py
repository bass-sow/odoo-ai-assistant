"""Retrieval-augmented answers with numbered citations."""

from dataclasses import dataclass

from app.rag.chunking import Chunk

SYSTEM_PROMPT = """You answer questions from warehouse staff using ONLY the numbered \
excerpts of the company's procedures given below.
- Cite the excerpts you use like [1] or [2][3].
- If the excerpts do not contain the answer, say so plainly. Do not make up steps.
- Be concise: short steps or a short paragraph. Answer in the language of the question."""

NO_ANSWER = (
    "I couldn't find this in the warehouse documentation. "
    "Try rephrasing, or ask your shift lead."
)


@dataclass
class Retrieved:
    chunk: Chunk
    score: float

    def to_dict(self) -> dict:
        return {
            "source": self.chunk.source,
            "title": self.chunk.title,
            "score": round(self.score, 3),
            "excerpt": self.chunk.text[:300],
        }


class Retriever:
    def __init__(self, embedder, store, k: int = 4, min_score: float = 0.35):
        if store.embedder_name != embedder.name:
            raise ValueError(
                f"Index was built with {store.embedder_name} but the embedder is "
                f"{embedder.name}. Run `python -m app.rag.ingest` again."
            )
        self.embedder, self.store, self.k, self.min_score = embedder, store, k, min_score

    def retrieve(self, query: str, k: int | None = None) -> list[Retrieved]:
        vec = self.embedder.embed([query])[0]
        hits = self.store.search(vec, k or self.k)
        return [Retrieved(c, s) for c, s in hits if s >= self.min_score]


def format_context(hits: list[Retrieved]) -> str:
    return "\n\n".join(f"[{i}] ({h.chunk.title})\n{h.chunk.text}" for i, h in enumerate(hits, 1))


def answer_question(question: str, retriever: Retriever, llm) -> dict:
    hits = retriever.retrieve(question)
    if not hits:
        # Nothing relevant: don't spend a call and don't give the model room to invent.
        return {"answer": NO_ANSWER, "sources": [], "grounded": False}
    result = llm.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Excerpts:\n\n{format_context(hits)}\n\nQuestion: {question}"},
        ],
        operation="ask",
    )
    return {
        "answer": (result.content or "").strip(),
        "sources": [h.to_dict() for h in hits],
        "grounded": True,
    }
