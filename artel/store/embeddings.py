import numpy as np
from fastembed import TextEmbedding

_model: TextEmbedding | None = None


def get_model() -> TextEmbedding:
    global _model
    if _model is None:
        _model = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
    return _model


def embed(text: str) -> list[float]:
    return next(get_model().embed([text])).tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.array(a)
    vb = np.array(b)
    return float(np.dot(va, vb))
