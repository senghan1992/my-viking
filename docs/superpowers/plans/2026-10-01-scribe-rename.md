# Scribe rename plan (2026-10-01)

## Map
- Folder `my-viking` → `scribe` (last step, via mv).
- Python pkg `jv/` → `scribe/`; imports `jv.*` → `scribe.*` (app, tests, tools).
  Console scripts: `scribe = scribe.cli:main` + compat alias `jv = scribe.cli:main`.
- pip name `myviking` → `scribe-kb`. `build/` output ignored (regenerate, not edited).
- Brand strings `myviking`/`my-viking` → `scribe` (caseVariants: MyViking→Scribe,
  MYVIKING→SCRIBE, my-viking→scribe) in py/html/mjs/yml/docs, except
  `.superpowers/` scratch and `build/`.
- Env `VIKING_*` → `SCRIBE_*` with old-name fallback read.
- Home `~/.myviking` → `~/.scribe` with old-dir fallback read (write new).
- Link files `.myviking-connection.json` → `.scribe-connection.json` (+secretary
  same) with old-name fallback read (write new).
- TS template: file `scribe.ts`, `/scribe` command, role env `SCRIBE_ROLE`
  with `MYVIKING_ROLE` fallback; install paths `~/.pi/agent/extensions/scribe.ts`.
- API routes `/api/v1/*` unchanged. DB schema unchanged.

## Fallback rule
Read: new first, old second. Write: new only. One release, no deprecation window.

## Verification
- `pytest tests/` green, `node tools/pi-extension-smoke.mjs` pass.
- `grep -ri myviking|my-viking` clean except `.superpowers/`, `build/`, legacy docs note.
- mv folder last; re-run suite from new path.
