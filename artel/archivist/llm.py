from .config import settings

_anthropic_client = None
_openai_client = None


def _api_key() -> str:
    if settings.archivist_api_key:
        return settings.archivist_api_key
    if settings.archivist_provider == "anthropic":
        return settings.anthropic_api_key
    return ""


def _default_model() -> str:
    return "claude-sonnet-4-6" if settings.archivist_provider == "anthropic" else "gpt-4o"


def is_configured() -> bool:
    return bool(_api_key())


async def complete(system: str, user: str, max_tokens: int = 2048) -> str:
    model = settings.archivist_model or _default_model()
    key = _api_key()

    if settings.archivist_provider == "anthropic":
        import anthropic

        global _anthropic_client
        if _anthropic_client is None:
            _anthropic_client = anthropic.AsyncAnthropic(api_key=key)
        msg = await _anthropic_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text

    import openai

    global _openai_client
    if _openai_client is None:
        kwargs: dict = {"api_key": key}
        if settings.archivist_base_url:
            kwargs["base_url"] = settings.archivist_base_url
        _openai_client = openai.AsyncOpenAI(**kwargs)
    resp = await _openai_client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content
