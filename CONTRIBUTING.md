# Contributing

## Setup

```bash
git clone https://github.com/NicolasPrimeau/artel
cd artel
uv sync --dev
pre-commit install
```

## Running tests

```bash
uv run pytest tests/ -v
```

## Code style

Ruff handles linting and formatting. Pre-commit runs it automatically on commit, or run manually:

```bash
uv run ruff check --fix .
uv run ruff format .
```

## Commits

Use [conventional commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `refactor:`, `docs:`, `test:`.

## Pull requests

Open a PR against `master`. CI must pass. For non-trivial changes, open an issue first to align on direction.
