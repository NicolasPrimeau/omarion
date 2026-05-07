from fastembed import TextEmbedding

_model: TextEmbedding | None = None


def get_model() -> TextEmbedding:
    global _model
    if _model is None:
        _model = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
    return _model


def embed(text: str) -> list[float]:
    return next(get_model().embed([text])).tolist()
