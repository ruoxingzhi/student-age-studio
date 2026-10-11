# AGENTS.md — StudentAge Studio

Local-first mod editor for the game StudentAge (Steam AppId `1991040`). GPL-3.0-only.
Backend + frontend live side by side in `standalone/`; no web framework, no JS bundler.

## Run / verify (exact commands)

- Setup: Python **3.12+**, `python -m pip install -r requirements.txt`, then `python -B standalone/server.py`
  (prints a localhost URL with a session token; never share the token).
  Windows also needs WebView2 Evergreen Runtime; Mac needs Xcode Command Line Tools.
- Full Python suite (what CI runs): `python -B -m unittest discover -s standalone/tests`
- One test file: `python -B -m unittest discover -s standalone/tests -p test_love_gift.py`
- JS tests run directly, no runner: `node standalone/tests/<name>.cjs`
  (`remote-load-order.cjs` needs `node --expose-gc`). Full JS list is in `.github/workflows/check.yml`.
- Update-package smoke test: `python -B tools/build_update.py --out release/student-age-studio-update.zip`
- CI installs only `certifi Pillow numpy imageio-ffmpeg` (no UnityPy/fmod) — keep tests passing without them.
- This environment runs **Windows PowerShell 5.1**: use `$env:NAME="..."`, never `export`; no `tail`/`grep`
  (use `Select-String`); quote paths containing spaces.

## Architecture (not obvious from filenames)

- `standalone/server.py` is backend + static host: `ThreadingHTTPServer` bound to `127.0.0.1`
  with hand-written `if route == ...` dispatch and `X-Studio-Token` auth (`hmac.compare_digest`).
  Long jobs return 202 + polling (see `ResourceJobs`), never blocking POSTs (socket timeout is 30s).
- Frontend is vanilla JS/CSS loaded by `standalone/index.html` (~50 scripts, `window.StudentAge*`
  globals, no `import`). `previewKind` in feature JSON is **inert metadata — no JS reads it**.
- `desktop/windows/` (WebView2/pywebview, PyInstaller) and `desktop/` (macOS WKWebView Swift)
  are separate shells; `update_bootstrap.py` (exit code 42 = restart for update) is stable ABI
  and must stay backward compatible.
- Errors use `ApiError(message, status, code)`; 409/`conflict` = stale revision, re-read instead of overwriting.
- Key docs: `docs/open-source-release.md` (release/update flow), `docs/ai-tools.md` (CLI/MCP),
  `docs/reviews/*.md` (integration decisions — read before touching those areas),
  `docs/workshop-publish.md` (Workshop upload design).

## Data-driven UI (how features get added without frontend code)

- `standalone/workshop-features.json` lists features; a feature is **hidden** unless its
  `primaryTable` schema has `nativePage: true` (`workshop_features()` in `server.py`).
  Unlisted `nativePage` tables get an auto-generated generic entry.
- `standalone/catalog-schema.json` holds table schemas; `allowIncomplete: true` puts a table into
  `gameplay_features.NAMES` (extractable originals). Adding extractable tables requires bumping
  the `gameplay-catalog.json` version in `gameplay_features.py` (currently 4) or clients reuse stale caches.
- `table_schema()` falls back to `catalog-schema.json` + `gameplay_features.schemas()` and **infers**
  unknown fields from row samples — new game fields appear as `advanced/inferred` automatically.

## Save semantics (do not "simplify" these)

- `TalkCfg`/`OptionCfg` must save via the story path (`talkPatch`/`deletedIds`), never direct
  `table_save` with `scope: local` (raises). A full talks map missing undeclared rows aborts 409 —
  never treat absence as deletion. Event deletion cascades only when a line's owners ⊆ removed set.
- `commit()` rolls back from the on-disk snapshot copy (rename, no new allocation), so rollback
  survives a full disk. `revision` is a content hash; any stale-revision write must 409, not merge.
- Original-mode reads tolerate a corrupt `original-edits.json` (empty view + warning); writes stay
  strict 422. `manifest.json` locks `metadata.id`/`packageId` (change via copy, not edit).
- Atomic writes: user data keeps `fsync=True`; only recomputable indexes may pass `fsync=False`.

## Tests (repo-specific harness)

- Every Python test bootstraps with `sys.path.insert(0, <standalone>)` and isolates state via
  `patch.dict(os.environ, {'STUDIO_CACHE_ROOT': tmp, ...})` — copy that pattern for new tests.
- **Windows sqlite gotcha**: close `store`/`OriginalDialogue` handles before `TemporaryDirectory`
  cleanup or teardown fails with `WinError 32` (a held read lock blocks unlink).
- Full `discover` can exceed local timeouts on Windows — run per-file with `-p` during development.
- Node `.cjs` tests use `vm` sandboxes + fixture JSON, no game install needed.

## Platform / domain gotchas (verified, expensive to rediscover)

- NTFS: rewriting file contents does **not** bump the parent directory mtime — directory stamps
  detect add/remove only; content change needs per-file fingerprints. (`st_ctime` on Windows is
  creation time; `file_fingerprint()` uses NTFS ChangeTime instead.)
- `ItemCfg / BookCfg` is a **union reference**, never a table-interface name; `GiftEvtCfg.npc/talkId`
  are index-aligned lists; missing/zero `type` **consumes** the gift.
- Love semantics (`docs/reviews/pr6-love-gift.md`): `LoveVindicateCfg.target` is a success-rate tier
  (not a favor threshold); `LoveActionCfg` keys are love-value unlocks (don't allocate new IDs);
  `LoveGreetingCfg` has no confirmed game caller — don't present it as functional.
- Import accepts BMP/TGA but converts to PNG; game-facing bytes stay PNG/JPEG/WebP. Cover art ≤ 1MB.
- Frontend `record-ids.js` `reserved` set must clear on project switch or IDs leak across projects.
- Steam: `steam_api64.dll` is proprietary — **never commit or ship it**; load from the user's game/Steam
  install at runtime. Vendored bridge DLLs need their LICENSE file + a `THIRD_PARTY_NOTICES.md` entry.

## Release (only when asked to release)

- Bump `APP_VERSION` in `standalone/error_logs.py` and commit to `main` first.
- Full clients require `.github/desktop-release.json` version == `APP_VERSION` at build time
  (they are intentionally out of sync between full releases); light updates ship via the `updates`
  branch — see `docs/open-source-release.md`. `tools/build_update.py` packs only top-level
  `standalone/` code files; `vendor-*/`, `docs/`, tests never enter the package.

## Collaboration sync (`sync_store.py`)

- Git-backed but git is invisible: user strings are all-Chinese, see the glossary in
  `docs/mod-sync.md` (showing any git term to users is a bug; `test_sync_store.py` asserts this).
- No `.git` inside mods: ledger is a bare repo under `user_data/sync/<hash>/`, driven with
  `--git-dir/--work-tree`. The relay is a bare repo in the user's own cloud folder.
- Never delete anything in the relay; merge keeps both sides (`合稿待选/`); sync only sends
  committed snapshots. Tests use real git (present in CI); they skip when git is absent.
- UI: `sync-ui.js` + `sync.css` mounted as workshop feature `sync` (same pattern as idle-chats);
  style only with `--sg-*` tokens so all six themes adapt. UI test `tests/sync-ui.cjs` runs
  in the Linux CI block of `check.yml`.
- Collaboration shards: `sync-team.json` partitions each table's ID range into 1024 shards
  (`sync_store.shard_interval` / `STUDIO_IDS.shardRange`, shared vectors in both test files).
  Solo mods (no team) take the unsharded path; derived tables and scaffold claims are exempt.

## Never commit

Game data/bundles, user mods, machine paths, caches, keys/tokens, `ui-assets/*` or `native/*`
(gitignored except READMEs — local-only), or screenshots containing private content.
