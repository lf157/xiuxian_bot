# Repository Guidelines

## Project Structure & Module Organization
- `core/`: main backend logic.
- `core/routes/`: Flask API blueprints grouped by domain (`combat.py`, `shop.py`, `sect.py`, etc.).
- `core/services/`: business workflows and settlement logic.
- `core/game/`: game data, progression systems, combat/event engines.
- `core/database/`: connection, schema, and migration helpers.
- `adapters/`: bot adapters (`telegram`, `aiogram`) and compatibility layers.
- `web_local/` and `web_public/`: admin/public Flask web surfaces.
- `tests/`: pytest suites; `scripts/`: smoke and regression scripts.
- Runtime/config files: `config.json`, `.env`, `CHANGELOG.md`, `start.py`.

## Build, Test, and Development Commands
- `uv sync`: install/update Python dependencies from `pyproject.toml` + `uv.lock`.
- `cp .env.example .env`: create local env config.
- `uv run python start.py`: start core service and enabled adapters/web processes.
- `uv run python -m pytest -q`: run full test suite.
- `pytest -m smoke -q`: quick regression pass.
- `uv run python scripts/smoke_api_e2e.py`: API smoke checks.
- `uv run python scripts/smoke_bot_layer.py`: bot-layer smoke checks (no network).

## Coding Style & Naming Conventions
- Python style: 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes.
- Keep route files domain-scoped (for example, new PvP endpoints go in `core/routes/pvp.py`).
- Avoid hardcoded tunables in services; put adjustable values in `config.json` with safe defaults/fallback parsing.
- When adding config keys, also update `web_local/static/i18n/config_fields_zh.json`.

## Testing Guidelines
- Framework: `pytest` (`pytest.ini` enforces `test_*.py`, `Test*`, `test_*`).
- Use markers intentionally: `smoke`, `slow`, `api`, `integration`.
- DB tests must target a test database (name should contain `test`) unless `XXBOT_TEST_ALLOW_DB_RESET=1` is explicitly set.

## Commit & Pull Request Guidelines
- History mixes short Chinese summaries and Conventional Commit prefixes (`feat:`, `fix:`, `chore:`). Prefer: `<type>: concise change summary`.
- Keep commits focused by module/feature.
- PRs should include: purpose, key behavior changes, test evidence (commands + result), and linked issue/task.
- For UI changes (`web_local/`, `web_public/`), include screenshots.
- Update `CHANGELOG.md` at the top in reverse chronological order with `YYYY-MM-DD HH:MM (UTC+8)`, affected files/modules, and summary.

## Security & Configuration Tips
- Never commit secrets from `.env`.
- Internal API calls must use `X-Internal-Token`; actor-sensitive routes also require `X-Actor-User-Id` consistency.
