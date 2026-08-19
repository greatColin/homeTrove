# User Instruction Memory

This file records project knowledge for future agents working on homeTrove.

## Format

### User Instruction Entry

User instruction entries should follow this format:

[User Instruction Summary]
- Date: [YYYY-MM-DD]
- Context: [Mentioned scenario or time]
- Instructions:
  - [Content of user teaching or instruction, described line by line]

### Project Knowledge Entry

Entries discovered by the Agent during task execution should follow this format:

[Project Knowledge Summary]
- Date: [YYYY-MM-DD]
- Context: Discovered by Agent while performing [specific task description]
- Category: [Operations & Deployment|Build Methods|Testing Methods|Troubleshooting & Debugging|Workflow & Collaboration|Environment Configuration]
- Instructions:
  - [Specific knowledge points, described line by line]

## Deduplication Strategy

- Before adding a new entry, check for similar or identical instructions.
- If a duplicate is found, skip the new entry or merge it with the existing one.
- When merging, update the context or date information.
- This helps avoid redundant entries and keeps the memory file tidy.

## Entries

[Project Knowledge Summary]
- Date: 2026-08-14
- Context: Completed v2 content encryption (vault) feature
- Category: Workflow & Collaboration
- Instructions:
  - All vault-related code lives in `hometrove/vault/` subpackage
  - 20 vault tests in `tests/test_vault.py` (all passing)
  - Full test suite: 156 tests, all passing
  - Vault commits on main: `28e6841` (initial), `637a021` (fixes), `7aa35f0` (Pillow compat), `62fe9a6` (Content-Length fix)
  - Never use `cd <dir> && command` pattern — use `workdir` parameter instead

[Project Knowledge Summary]
- Date: 2026-08-14
- Context: Vault encrypted asset HTTP endpoints
- Category: Troubleshooting & Debugging
- Instructions:
  - Encrypted asset file endpoints (asset_file, public_file) use async generator directly with StreamingResponse — no `_collect()` wrapper, no Content-Length header
  - Range requests on encrypted assets are not supported (encryption is seek-agnostic) — vault returns full decrypted stream via transfer-encoding: chunked
  - Locked vault returns placeholder image (200 OK) for encrypted assets, not 401/404
  - `resolve_asset_path` in `plugins/api.py` decrypts encrypted assets to a temp file (cleanup via `atexit`) when vault is unlocked

[Project Knowledge Summary]
- Date: 2026-08-14
- Context: Production database migration for vault
- Category: Operations & Deployment
- Instructions:
  - If `alembic_version` table is empty after migration, manually set: `UPDATE alembic_version SET version_num='0009_vault_state' WHERE id=1;`
  - If `assets` table is missing columns (`encrypted_path`, `encrypted_nonce`, `origin_path`, `filename`), run: `ALTER TABLE assets ADD COLUMN encrypted_path TEXT; ALTER TABLE assets ADD COLUMN encrypted_nonce BLOB; ALTER TABLE assets ADD COLUMN origin_path TEXT; ALTER TABLE assets ADD COLUMN filename TEXT;`
  - If `vault_state` has an empty placeholder row, delete it before restarting so lifespan can reinitialize

[Project Knowledge Summary]
- Date: 2026-08-14
- Context: Pillow compatibility issue in vault placeholders
- Category: Troubleshooting & Debugging
- Instructions:
  - Pillow 10+ removed `outline=` parameter from `ImageDraw.arc`; use `width=` parameter instead
  - `_ensure_lock_glyph_np` in `vault/placeholders.py` line 122 had this issue — fixed in `7aa35f0`

[Project Knowledge Summary]
- Date: 2026-08-19
- Context: Vault session and cookie behavior
- Category: Environment Configuration
- Instructions:
  - Vault session cookie: `hometrove_vault`, HttpOnly + SameSite=Strict + TTL 7 days (configurable via `HOMETROVE_VAULT_SESSION_TTL_SECONDS`)
  - Process restart invalidates all vault sessions (session stored in-process, not Redis)
  - Vault is enabled by default (`HOMETROVE_VAULT_ENABLED=true`); set `HOMETROVE_VAULT_ENABLED=off` to disable
  - Fresh servers start in `initialized` state; run `hometrove-cli vault setup --password <pwd> --confirm <pwd>` before encrypted uploads
  - Encrypted assets store `path` as `vault\0{encrypted_path}` so the unique constraint is satisfied

[Project Knowledge Summary]
- Date: 2026-08-19
- Context: Asset vs AssetLike distinction in vault code
- Category: Build Methods
- Instructions:
  - `AssetLike` now includes `encrypted_path` and `filename` fields so plugins can resolve encrypted assets transparently
  - `resolve_asset_path` in `plugins/api.py` decrypts encrypted assets to a temp file (cleanup via `atexit`) when vault is unlocked
  - `is_asset_encrypted()` in `vault/read.py` accepts any object with a truthy `encrypted_path` attribute

[Project Knowledge Summary]
- Date: 2026-08-14
- Context: Scanner vault auto-import
- Category: Environment Configuration
- Instructions:
  - Scanner vault auto-import is controlled by `HOMETROVE_VAULT_AUTO_IMPORT=true` (default false)
  - When enabled and vault is unlocked, scanner stream-encrypts new files into `vault/v/{HH}/{HH}/{random}.c9r`
  - Existing assets are never re-encrypted; only new discoveries are encrypted
  - Old plaintext assets remain readable when vault is enabled

[Project Knowledge Summary]
- Date: 2026-08-19
- Context: Running the project
- Category: Operations & Deployment
- Instructions:
  - Backend: `hometrove serve` (vault encryption is enabled by default)
  - Frontend dev: `cd web && npm run dev` (or use deploy-website skill for preview)
  - System dependencies for OpenCV / PyAV are listed in `requirements-system.txt`:
    `libxcb1 libxcb-shape0 libxcb-xfixes0 libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1`
  - Run tests: `PYTHONPATH=/workspace python3 -m pytest tests/ -q`
  - Full suite result: 170 passed, 1 skipped (root-only read-only probe skip), ~4 min
  - Environment Python is 3.11.2 but project requires >=3.12; workaround is `PYTHONPATH=/workspace python3 -m hometrove.cli_agent ...`

[Project Knowledge Summary]
- Date: 2026-08-19
- Context: Added homeTrove agent CLI (`hometrove-cli`)
- Category: Operations & Deployment
- Instructions:
  - CLI entry point: `python3 -m hometrove.cli_agent <subcommand>` (or `hometrove-cli` after `pip install -e .`)
  - Required env vars: `HOMETROVE_CLI_HOST` and `HOMETROVE_CLI_API_KEY`
  - Server enables token auth automatically when `HOMETROVE_CLI_API_KEY` is set
  - Common commands:
    - `hometrove-cli endpoints` / `describe <cmd>`
    - `hometrove-cli upload <file> [--plugins p1,p2] [--no-encrypted]`
    - `hometrove-cli search <query> [--limit N]` and `search --mode filter --tag ...`
    - `hometrove-cli test-plugin <plugin_id> <file>`
    - `hometrove-cli vault status/setup/unlock/lock`
  - Encrypted uploads require the server vault to be set up and unlocked; the CLI keeps the unlock cookie in-memory only
  - CLI tests live in `tests/test_agent_cli.py`; integration tests start a real server subprocess
