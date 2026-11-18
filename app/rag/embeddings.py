"""Embedding backends. All return L2-normalised float32 vectors, so cosine = dot product."""

import hashlib
import re

import numpy as np


def _normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class OpenAICompatEmbedder:
    def __init__(self, base_url: str, api_key: str, model: str, batch_size: int = 50):
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-needed", max_retries=3)
        self.model = model
        self.batch_size = batch_size
        self.name = f"api:{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            resp = self._client.embeddings.create(model=self.model, input=texts[i : i + self.batch_size])
            data = resp.data
            # Gemini's OpenAI-compatible endpoint leaves `index` empty; the list is
            # already in input order then, so only sort when every item has one.
            if all(d.index is not None for d in data):
                data = sorted(data, key=lambda d: d.index)
            out.extend(d.embedding for d in data)
        return _normalise(np.array(out, dtype=np.float32))


_TOKEN = re.compile(r"[a-z0-9áéíóúñàèç]+")
_STOP = set("the a an of to and or in on for is are be with by at from as it this that".split())


class HashEmbedder:
    """Offline embedder: hashed unigrams + bigrams (feature hashing).

    It only captures word overlap, not meaning, but it is deterministic and free,
    which makes it good for tests, CI and running the project with no API key.
    """

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.name = f"local:hash-{dim}"

    def _features(self, text: str) -> list[str]:
        words = [w for w in _TOKEN.findall(text.lower()) if w not in _STOP]
        stems = [w[:6] for w in words]  # crude stemming: "expiring" ~ "expired"
        return stems + [f"{a}_{b}" for a, b in zip(stems, stems[1:], strict=False)]

    def embed(self, texts: list[str]) -> np.ndarray:
        m = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat in self._features(text):
                h = int(hashlib.md5(feat.encode()).hexdigest(), 16)
                m[row, h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
        return _normalise(m)


def build_embedder(settings):
    if settings.embed_provider == "local":
        return HashEmbedder()
    provider = settings.provider(settings.embed_provider)
    if not provider or not settings.embedding_model:
        raise ValueError(f"Provider '{settings.embed_provider}' has no embeddings endpoint")
    key = settings.embed_api_key or settings.llm_api_key
    return OpenAICompatEmbedder(provider["base_url"], key, settings.embedding_model)
