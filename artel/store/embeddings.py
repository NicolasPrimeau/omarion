from fastembed import TextEmbedding

_model: TextEmbedding | None = None
_model_failed = False


def get_model() -> TextEmbedding | None:
    global _model, _model_failed
    if _model_failed:
        return None
    if _model is None:
        try:
            _model = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
        except Exception:
            _model_failed = True
            return None
    return _model


def embed(text: str) -> list[float] | None:
    model = get_model()
    if model is None:
        return None
    try:
        return next(model.embed([text])).tolist()
    except Exception:
        return None
