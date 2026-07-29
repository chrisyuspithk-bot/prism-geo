"""Embedding: tries API first, falls back to character n-gram hashing (zero-deps)."""

import json
import re

_DIM = 384
_CACHE: dict[str, list[float]] = {}
_FASTEMBED = None


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

    # Try fastembed first
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
