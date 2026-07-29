"""Embedding: tries Jina API first (if key set), then fastembed, falls back to character n-gram hashing."""

import httpx
import json
import os
import re

_DIM = 2048  # Jina v4 default; n-gram fallback also uses this
_CACHE: dict[str, list[float]] = {}
_FASTEMBED = None
_JINA_KEY: str | None = None
_JINA_CHECKED = False


def _get_jina_key() -> str | None:
    global _JINA_KEY, _JINA_CHECKED
    if _JINA_CHECKED:
        return _JINA_KEY
    _JINA_CHECKED = True
    try:
        from .db import connect
        with connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'embedding_api_key'").fetchone()
        _JINA_KEY = (row["value"] or "").strip() if row else ""
    except Exception:
        pass
    if not _JINA_KEY:
        _JINA_KEY = os.environ.get("JINA_API_KEY", "")
    return _JINA_KEY or None


def _jina_embed(texts: list[str]) -> list[list[float]] | None:
    key = _get_jina_key()
    if not key:
        return None
    try:
        resp = httpx.post(
            "https://api.jina.ai/v1/embeddings",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "jina-embeddings-v4", "task": "text-matching",
                  "input": [{"text": t} for t in texts]},
            timeout=30,
        )
        data = resp.json()
        return [d["embedding"] for d in data.get("data", [])]
    except Exception:
        return None


def _ngram_hash(text: str, dim: int = _DIM) -> list[float]:
    """Deterministic character 3-gram hash embedding. No model, no deps, ~0.1ms."""
    text = text.lower().strip()
    vec = [0.0] * dim
    # Character trigrams
    for i in range(len(text) - 2):
        h = hash(text[i:i+3]) % dim
        vec[h] += 1.0
    # Word bigrams for semantic signal
    words = re.findall(r'\w+', text)
    for i in range(len(words) - 1):
        h = hash(words[i] + "_" + words[i+1]) % dim
        vec[h] += 0.7
    # Normalize
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _try_fastembed() -> bool:
    global _FASTEMBED
    if _FASTEMBED is not None:
        return True
    try:
        from fastembed import TextEmbedding
        from pathlib import Path
        cache = Path(__file__).resolve().parent.parent / ".cache" / "fastembed"
        cache.mkdir(parents=True, exist_ok=True)
        _FASTEMBED = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=str(cache))
        return True
    except Exception:
        return False


def embed(texts: list[str]) -> list[list[float]]:
    results: list[list[float]] = []
    to_compute: list[tuple[int, str]] = []

    for i, t in enumerate(texts):
        if t in _CACHE:
            results.append(_CACHE[t])
        else:
            to_compute.append((i, t))
            results.append([])

    if not to_compute:
        return results

    # Try Jina API first (fast, remote)
    jina_vecs = _jina_embed([t for _, t in to_compute])
    if jina_vecs and len(jina_vecs) == len(to_compute):
        for (idx, text), vec in zip(to_compute, jina_vecs):
            _CACHE[text] = vec
            results[idx] = vec
        return results

    # Try fastembed (local, requires model download)
    if _try_fastembed():
        try:
            vecs = list(_FASTEMBED.embed([t for _, t in to_compute]))
            for (idx, text), vec in zip(to_compute, vecs):
                v = vec.tolist() if hasattr(vec, "tolist") else list(vec)
                _CACHE[text] = v
                results[idx] = v
            return results
        except Exception:
            pass

    # Fallback to n-gram hashing
    for idx, text in to_compute:
        v = _ngram_hash(text)
        _CACHE[text] = v
        results[idx] = v
    return results


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def pack(vec: list[float]) -> str:
    return json.dumps(vec)


def unpack(s: str) -> str:
    return json.loads(s)
