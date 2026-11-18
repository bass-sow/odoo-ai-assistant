"""Vector stores.

NumpyStore: brute-force cosine search over a matrix saved to disk. For a few thousand
chunks it is faster than any network call and it fits in a Lambda with the index in S3.

PgVectorStore: PostgreSQL + pgvector, for when the corpus grows or several instances
must share the same index.
"""

import json
from pathlib import Path
from typing import Protocol

import numpy as np

from app.rag.chunking import Chunk


class VectorStore(Protocol):
    embedder_name: str

    def add(self, chunks: list[Chunk], vectors: np.ndarray) -> None: ...
    def search(self, vector: np.ndarray, k: int) -> list[tuple[Chunk, float]]: ...
    def count(self) -> int: ...


class NumpyStore:
    def __init__(self, embedder_name: str):
        self.embedder_name = embedder_name
        self.chunks: list[Chunk] = []
        self.vectors = np.zeros((0, 0), dtype=np.float32)

    def add(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        if not chunks:
            return
        self.vectors = vectors if self.vectors.size == 0 else np.vstack([self.vectors, vectors])
        self.chunks.extend(chunks)

    def search(self, vector: np.ndarray, k: int) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        scores = self.vectors @ vector.astype(np.float32)
        top = np.argsort(-scores)[:k]
        return [(self.chunks[i], float(scores[i])) for i in top]

    def count(self) -> int:
        return len(self.chunks)

    def save(self, directory: str) -> None:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        np.save(d / "vectors.npy", self.vectors)
        (d / "chunks.json").write_text(json.dumps([c.to_dict() for c in self.chunks]))
        (d / "meta.json").write_text(
            json.dumps({"embedder": self.embedder_name, "count": len(self.chunks)})
        )

    @classmethod
    def load(cls, directory: str) -> "NumpyStore":
        d = Path(directory)
        meta = json.loads((d / "meta.json").read_text())
        store = cls(meta["embedder"])
        store.vectors = np.load(d / "vectors.npy")
        store.chunks = [Chunk(**c) for c in json.loads((d / "chunks.json").read_text())]
        return store


class PgVectorStore:
    def __init__(self, dsn: str, embedder_name: str, dim: int):
        import psycopg

        self.embedder_name = embedder_name
        self.dim = dim
        self.conn = psycopg.connect(dsn, autocommit=True)
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""CREATE TABLE IF NOT EXISTS doc_chunks (
                    id text PRIMARY KEY,
                    source text NOT NULL,
                    title text NOT NULL,
                    text text NOT NULL,
                    embedder text NOT NULL,
                    embedding vector({dim}) NOT NULL)"""
            )

    @staticmethod
    def _literal(v: np.ndarray) -> str:
        return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

    def reset(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM doc_chunks WHERE embedder = %s", (self.embedder_name,))

    def add(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO doc_chunks (id, source, title, text, embedder, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s::vector)
                   ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding,
                                                  embedder = EXCLUDED.embedder""",
                [
                    (c.id, c.source, c.title, c.text, self.embedder_name, self._literal(v))
                    for c, v in zip(chunks, vectors, strict=True)
                ],
            )

    def search(self, vector: np.ndarray, k: int) -> list[tuple[Chunk, float]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT id, text, source, title, 1 - (embedding <=> %s::vector) AS score
                   FROM doc_chunks WHERE embedder = %s
                   ORDER BY embedding <=> %s::vector LIMIT %s""",
                (self._literal(vector), self.embedder_name, self._literal(vector), k),
            )
            return [
                (Chunk(id=r[0], text=r[1], source=r[2], title=r[3]), float(r[4]))
                for r in cur.fetchall()
            ]

    def count(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM doc_chunks WHERE embedder = %s", (self.embedder_name,))
            return cur.fetchone()[0]


def download_index_from_s3(bucket: str, prefix: str, target_dir: str) -> None:
    import boto3

    s3 = boto3.client("s3")
    Path(target_dir).mkdir(parents=True, exist_ok=True)
    for name in ("vectors.npy", "chunks.json", "meta.json"):
        s3.download_file(bucket, f"{prefix}{name}", str(Path(target_dir) / name))
