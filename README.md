![Scribe](assets/banner.svg)

[![License: MIT](https://img.shields.io/badge/License-MIT-brass.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Docker](https://img.shields.io/badge/docker-single%20container-2496ED.svg)](docker-compose.yml)
[![Tests](https://img.shields.io/badge/tests-129%20passing-green.svg)](tests/)

# Scribe

**Scribe gives your `pi` coding sessions a scribe.** You code — a second pi
session watches from the side, writes down only what's worth keeping, and hands
it back next time so you never make the same mistake twice.

- **Opt-in per folder, like git.** No link file, no recording, no injection.
  Installed but unused in a folder means exactly that: unused.
- **The worker never judges.** Work sessions only leave *observations*.
  A separate **scribe session** reads the inbox, checks the real transcripts,
  and files away just four things: recurring mistakes, repeated requests,
  decisions with reasons, reusable procedures.
- **No model key on the server.** Judgment happens in your own pi runtime.
  The server is storage, search, and a deterministic repeat-counter.
- **Adapts.** Injected knowledge that proves out becomes established;
  knowledge that misfires drops to needs-verification.

한국어 안내는 [README.ko.md](README.ko.md) 를 보세요.

---

## Quickstart — one container

```bash
git clone https://github.com/senghan1992/scribe.git
cd scribe
cp .env.example .env   # optional — ports, signup, keys (works with defaults)
docker compose up -d --build
```

Open `http://<server-ip>:8787/` → **Sign up** (first user becomes admin) →
**New project** → the **Connect** tab shows a one-liner with your key baked in:

```bash
curl -fsSL http://<server>:8787/install.sh | bash -s -- --url http://<server>:8787 --key sc_xxxx --project <slug>
```

Run it in a project folder. From then on, opening `pi` in that folder
attaches the library automatically. Folders without a link file stay free —
no tools, no briefing, no recording.

## Attaching the scribe

`scribe connect` links **this folder only** (like `git remote add`).
Everything is folder-scoped — there is no global "current project".

| Command | Does what |
|---|---|
| `scribe connect` | Ask for URL + key, link this folder, install extensions |
| `scribe status` | This folder: linked/free + scribe state, one screen |
| `scribe disconnect` | Unlink this folder (keys stay saved) |
| `scribe connect <name>` / `scribe switch <name>` | Point this folder at another saved project |
| `scribe list` | Saved connections (keys live in `~/.scribe/`, mode 0600) |
| `scribe disable` / `scribe enable` | Whole-machine OFF/ON switch |
| `scribe secretary once` | Wake the scribe now (background) |
| `scribe secretary auto on --every 8` | Let work sessions wake the scribe every 8 observations |
| `scribe secretary status` | Pending observations · repeat candidates · last report |
| `scribe inbox` | Peek at the pending inbox |

Per-folder scribe control — folder A auto-files while folder B stays manual:

```bash
cd ~/project-a
scribe secretary auto on --folder --every 5   # stored in .scribe-secretary.json
cd ~/project-b
scribe secretary auto off --folder            # this folder: manual only
```

Inside `pi`, the same goes through `/scribe`: `/scribe secretary once`,
`/scribe secretary status`, `/scribe note "worth keeping"`.

## How it works

```
 work session (pi)              server (1 container)            scribe session (pi, separate)
 ─────────────────              ──────────────────              ────────────────────────
 I just code                     store · search · counters       reads the inbox and judges
 turn traces as observations ──▶ · observations (inbox)      ◀── · GET /inbox (what's pending)
 creates no knowledge            · fingerprint/repeat math       · reads session transcripts
                                  · memories (L0/L1/L2)      ──▶ · POST /remember (keepers only)
                                  · brief/prepare → inject      · POST /inbox/ack (rest)
```

| Moment | Who | What happens |
|---|---|---|
| Session start | worker | `brief` — established + needs-verification + scribe backlog |
| Each question | worker | `prepare` — related knowledge packed by tier (L0/L1/L2) |
| Answer done | worker | `observe` — prompt/answer/files/errors as an observation |
| Server math | server | same-request fingerprints counted → repeats, recurring errors (no model) |
| Cleanup | **scribe** | `inbox` + transcripts → `remember` → `ack` |
| Next question | worker | knowledge that just failed drops to needs-verification |

The scribe files only four kinds of things (`pitfalls`, `knowledge`/`commands`,
`decisions`). One-offs, restatements, guesses, and secrets are discarded.
The scribe session gets **read-only tools** — it cannot touch your code —
and never records its own work (no feedback loop).

## Configuration

`docker compose` reads root `.env`; Portainer reads stack variables.
Everything works empty (extractive summaries, keyword search fallback).

| Variable | Default | Purpose |
|---|---|---|
| `SCRIBE_PORT` | 8787 | Exposed port |
| `SCRIBE_BASE_URL` | empty | Public URL shown in connect guides |
| `SCRIBE_ALLOW_SIGNUP` | true | Set false to close signup |
| `SCRIBE_FIRST_USER_ADMIN` | true | First signup becomes admin |
| `SCRIBE_ADMIN_EMAILS` | empty | Pinned admins, comma-separated |
| `SCRIBE_SECRET` | auto | Session signing (kept on volume) |
| `SCRIBE_LLM_*` / `SCRIBE_EMBED_*` | empty | Optional one-line summaries / semantic search |

No model is required for the scribe loop — it reuses your pi runtime.
Admins can also set models at **Admin → Model settings** (hot-swapped, no restart).

## Development

```bash
pip install -e .[dev]
python -m uvicorn app.main:app --port 8787   # SCRIBE_DATA=./data SCRIBE_SECRET=dev
pytest                                        # 129 tests
node tools/pi-extension-smoke.mjs             # 26 checks — template loaded for real
```

Data lives in one SQLite file on the `scribe-data` volume. Backup = volume
snapshot or per-project `export.md`. Processed observations auto-expire after
14 days; full transcripts stay on the agent machine.

## Migrating from myviking (v1 name)

Read path first, write new: existing `~/.myviking`,
`.myviking-connection.json` links, `MYVIKING_*`
env, and `jv_`-style keys keep working untouched. New writes go to `~/.scribe`,
`.scribe-connection.json`, `SCRIBE_*`. To finish the move:

```bash
mv ~/.myviking ~/.scribe          # one-time
# then re-run the connect one-liner per folder (or leave the old links — they still read)
```

Old pi extensions (`myviking.ts`) are removed automatically on the next
`scribe connect`. Rename the GitHub repo to `scribe` and old clone URLs keep
redirecting.

## License

MIT — see [LICENSE](LICENSE).
