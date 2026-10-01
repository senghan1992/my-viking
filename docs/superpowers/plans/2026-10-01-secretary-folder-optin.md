# Secretary Folder Opt-in Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 폴더마다 서기 사용 여부를 git처럼 결정할 수 있게 한다.

**Architecture:** 폴더 파일(`.myviking-secretary.json`)이 전역(`~/.myviking/config.json`)을 덮어쓴다. 서버 변경 없음. 파손·오프라인은 fail-open(자유 사용 + 경고).

**Tech Stack:** Python (jv/cli.py), TypeScript template (pi hub `myviking.ts` in same file), pytest, node smoke (`tools/pi-extension-smoke.mjs`).

## Global Constraints

- 비밀을 폴더 파일에 쓰지 않는다 (연결 id·auto/every만).
- 코딩 세션을 막지 않는다 (fail-open).
- 전역 `disable`이면 폴더 설정과 무관하게 전부 OFF.
- `every`는 1 이상으로 clamp.

---

### Task 1: 폴더 서기 설정 코어

**Files:**
- Modify: `jv/cli.py` (secretary section near `_secretary_home`, `secretary_auto`, `_link_folder`)
- Test: `tests/test_secretary_cli.py` (append new tests)

**Interfaces:**
- Consumes: `_project_link(cwd)`, `_project_root(cwd)`, `_git_exclude_add(cwd, pattern)`, `_load_config()`
- Produces: `_secretary_folder_file(cwd: Path) -> Path`, `_secretary_effective(cwd: Path | None) -> dict` (returns `{"auto": bool, "every": int, "source": "folder"|"global"|"default"}`)

- [ ] **Step 1: Write the failing test**

```python
def test_secretary_effective_folder_overrides_global(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".myviking").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    work = tmp_path / "proj"
    work.mkdir()
    (home / ".myviking" / "config.json").write_text('{"secretary": {"auto": false, "every": 12}}')
    (work / ".myviking-secretary.json").write_text('{"auto": true, "every": 5}')
    eff = cli._secretary_effective(work)
    assert eff == {"auto": True, "every": 5, "source": "folder"}


def test_secretary_effective_broken_folder_falls_back(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".myviking").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    work = tmp_path / "proj"
    work.mkdir()
    (home / ".myviking" / "config.json").write_text('{"secretary": {"auto": true, "every": 8}}')
    (work / ".myviking-secretary.json").write_text('{broken')
    eff = cli._secretary_effective(work)
    assert eff["auto"] is True and eff["source"] == "global"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_secretary_cli.py::test_secretary_effective_folder_overrides_global -v`
Expected: FAIL with "has no attribute '_secretary_effective'"

- [ ] **Step 3: Write minimal implementation**

```python
_SECRETARY_FOLDER_FILE = ".myviking-secretary.json"

def _secretary_folder_file(cwd: Path) -> Path:
    return _project_link(cwd).parent / _SECRETARY_FOLDER_FILE


def _secretary_effective(cwd: Path | None = None) -> dict:
    default = {"auto": False, "every": 12, "source": "default"}
    glob = (_load_config().get("secretary") or {})
    eff = {"auto": bool(glob.get("auto", False)),
           "every": max(1, int(glob.get("every", 12) or 12)),
           "source": "global" if glob else "default"}
    if cwd is None:
        try:
            cwd = Path(os.getcwd())
        except OSError:
            return eff
    try:
        p = _secretary_folder_file(Path(cwd))
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and ("auto" in data or "every" in data):
                return {"auto": bool(data.get("auto", eff["auto"])),
                        "every": max(1, int(data.get("every", eff["every"]) or eff["every"])),
                        "source": "folder"}
    except (OSError, ValueError):
        pass
    return eff if glob else default
```

Place next to `_secretary_home()`. Also call `_git_exclude_add(cwd, _SECRETARY_FOLDER_FILE)` inside `_link_folder()` after existing exclude call.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_secretary_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jv/cli.py tests/test_secretary_cli.py
git commit -m "feat: folder-level secretary override"
```

### Task 2: status 한 줄 + auto 명령 폴더 대응

**Files:**
- Modify: `jv/cli.py` (`status()`, `secretary_auto()`, `_agent_states()`)
- Test: `tests/test_secretary_cli.py`

**Interfaces:**
- Consumes: Task 1 `_secretary_effective(cwd)`
- Produces: `status` output line `이 폴더 서기: 자동 ON (관찰 5건마다, folder)` or `이 폴더 서기: 수동 (global)`

- [ ] **Step 1: Write the failing test**

```python
def test_status_shows_folder_secretary(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    work = tmp_path / "proj"
    work.mkdir()
    (work / ".myviking-secretary.json").write_text('{"auto": true, "every": 5}')
    args = _Args()
    args.cwd = str(work)
    cli.status(args)
    out = capsys.readouterr().out
    assert "이 폴더 서기" in out and "5" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_secretary_cli.py::test_status_shows_folder_secretary -v`
Expected: FAIL (output mismatch)

- [ ] **Step 3: Write minimal implementation**

```python
# status() 연결 표시 직후에 추가:
eff = _secretary_effective(cwd)
src = {"folder": "이 폴더 설정", "global": "전역 설정", "default": "기본값"}[eff["source"]]
auto_txt = f"자동 ON (관찰 {eff['every']}건마다)" if eff["auto"] else "수동"
print(f"이 폴더 서기: {auto_txt} · {src}")
```

And in `secretary_auto()`: add `--folder` flag handling — when `getattr(args, "folder", False)` and cwd link root known, write `.myviking-secretary.json` instead of global config. Parser change: `sps.add_argument("--folder", action="store_true")` on the auto subparser.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_secretary_cli.py tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jv/cli.py tests/test_secretary_cli.py
git commit -m "feat: status shows folder secretary, auto --folder"
```

### Task 3: pi 확장 폴더값 반영 + 스모크

**Files:**
- Modify: `jv/cli.py` (`_PI_EXT_TEMPLATE` `secretarySetting` function)
- Test: `tools/pi-extension-smoke.mjs` (add check), `pytest tests/test_secretary_cli.py`

**Interfaces:**
- Consumes: Task 1 folder file name `.myviking-secretary.json`
- Produces: TS `secretarySetting(cwd)` reads folder file first, falls back to global config

- [ ] **Step 1: Write the failing check**

```js
// tools/pi-extension-smoke.mjs — expected checks add:
assert(src.includes('.myviking-secretary.json'), 'folder secretary override missing');
```

- [ ] **Step 2: Run to verify it fails**

Run: `node tools/pi-extension-smoke.mjs`
Expected: FAIL with "folder secretary override missing"

- [ ] **Step 3: Write minimal implementation**

```ts
function secretarySetting(cwd: string): { auto: boolean; every: number } {
  const fallback = (() => {
    try {
      const s = JSON.parse(readFileSync(CONFIG_FILE, "utf8"))?.secretary || {};
      return { auto: s.auto === true, every: Math.max(1, Number(s.every) || 12) };
    } catch { return { auto: false, every: 12 }; }
  })();
  try {
    let dir = resolve(cwd || process.cwd());
    for (let i = 0; i < 12; i++) {
      const f = join(dir, ".myviking-secretary.json");
      if (existsSync(f)) {
        const j = JSON.parse(readFileSync(f, "utf8"));
        if (j && (typeof j.auto !== "undefined" || typeof j.every !== "undefined"))
          return { auto: typeof j.auto === "undefined" ? fallback.auto : !!j.auto,
                   every: Math.max(1, Number(j.every) || fallback.every) };
        return fallback;
      }
      if (existsSync(join(dir, ".git"))) return fallback;
      const parent = resolve(dir, "..");
      if (parent === dir) return fallback;
      dir = parent;
    }
  } catch { /* fall through */ }
  return fallback;
}
```

Update the `observe()` caller to pass `process.cwd()`.

- [ ] **Step 4: Run to verify it passes**

Run: `node tools/pi-extension-smoke.mjs`
Expected: all checks pass (22+1)

- [ ] **Step 5: Commit**

```bash
git add jv/cli.py tools/pi-extension-smoke.mjs
git commit -m "feat: pi extension honors folder secretary setting"
```
