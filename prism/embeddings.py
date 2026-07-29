"""Embedding manager using fastembed (ONNX, no GPU needed)."""

import json
from pathlib import Path

_CACHE: dict[str, list[float]] = {}

_EMBEDDER = None
_DIM = 384


def _ensure():
    global _EMBEDDER
    if _EMBEDDER is None:
        from fastembed import TextEmbedding
        _EMBEDDER = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=str(_cache_dir()))


def _cache_dir() -> Path:
    p = Path(__file__).resolve().parent.parent / ".cache" / "fastembed"
    p.mkdir(parents=True, exist_ok=True)
    return p


def embed(texts: list[str]) -> list[list[float]]:
    """Return 384-dim embeddings. Caches results by text content."""
    results: list[list[float]] = []
    to_embed: list[str] = []
    indices: list[int] = []

    for i, t in enumerate(texts):
        if t in _CACHE:
            results.append(_CACHE[t])
        else:
            to_embed.append(t)
            indices.append(i)
            results.append([])  # placeholder

    if to_embed:
        _ensure()
        for j, vec in enumerate(_EMBEDDER.embed(to_embed)):
            v = vec.tolist() if hasattr(vec, "tolist") else list(vec)
            _CACHE[to_embed[j]] = v
            results[indices[j]] = v

    return results


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def pack(vec: list[float]) -> str:
    return json.dumps(vec)


def unpack(s: str) -> list[float]:
    return json.loads(s)
