"""Build the vector index from data/docs.

    python -m app.rag.ingest            # numpy index in data/index
    python -m app.rag.ingest --upload   # ...and push it to S3_BUCKET
"""

import argparse
import logging
import time

from app.config import get_settings
from app.rag.chunking import load_documents
from app.rag.embeddings import build_embedder
from app.rag.store import NumpyStore, PgVectorStore

log = logging.getLogger("ingest")


def ingest(settings=None) -> int:
    settings = settings or get_settings()
    embedder = build_embedder(settings)
    chunks = load_documents(settings.docs_dir)
    if not chunks:
        raise SystemExit(f"No documents found in {settings.docs_dir}")

    start = time.perf_counter()
    vectors = embedder.embed([c.text for c in chunks])
    log.info("Embedded %d chunks with %s in %.1fs", len(chunks), embedder.name,
             time.perf_counter() - start)

    if settings.vector_store == "pgvector":
        store = PgVectorStore(settings.database_url, embedder.name, vectors.shape[1])
        store.reset()
        store.add(chunks, vectors)
    else:
        store = NumpyStore(embedder.name)
        store.add(chunks, vectors)
        store.save(settings.index_dir)
    return len(chunks)


def upload(settings) -> None:
    import boto3

    s3 = boto3.client("s3")
    for name in ("vectors.npy", "chunks.json", "meta.json"):
        s3.upload_file(f"{settings.index_dir}/{name}", settings.s3_bucket,
                       f"{settings.s3_index_prefix}{name}")
    log.info("Index uploaded to s3://%s/%s", settings.s3_bucket, settings.s3_index_prefix)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--upload", action="store_true", help="upload the index to S3_BUCKET")
    args = parser.parse_args()
    s = get_settings()
    n = ingest(s)
    print(f"Indexed {n} chunks ({s.vector_store}, embeddings: {s.embed_provider})")
    if args.upload:
        upload(s)
