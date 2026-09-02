"""``jv`` — the command line for MyViking.

Grouped so the shape of the system is visible from ``jv --help``:

    jv project ...   프로젝트와 메모리 프로파일
    jv prompt  ...   프롬프트 라이브러리
    jv mem     ...   메모리 직접 조작
    jv ask     ...   컨텍스트를 조립해 요청 준비 (캐시 확인 포함)
    jv commit  ...   답변 기록 → 자가학습 투입
    jv ls/tree/find/grep/read   저장소 탐색
    jv report/stats/cache       토큰 회계
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .budget import format_report
from .config import Config
from .connect import CLIENTS
from .profiles import templates
from .service import Jarvis
from .tokens import estimate_tokens

# --------------------------------------------------------------------------
# output helpers
# --------------------------------------------------------------------------
def _out(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return
    print(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _table(rows: list[dict[str, Any]], cols: list[tuple[str, str]]) -> str:
    if not rows:
        return "(없음)"
    widths = {}
    for key, label in cols:
        widths[key] = max(len(label), *(len(str(r.get(key, ""))) for r in rows))
    head = "  ".join(label.ljust(widths[key]) for key, label in cols)
    sep = "  ".join("-" * widths[key] for key, _ in cols)
    body = "\n".join(
        "  ".join(str(r.get(key, "")).ljust(widths[key]) for key, _ in cols) for r in rows
    )
    return f"{head}\n{sep}\n{body}"


def _read_text_arg(value: str | None, file: str | None) -> str:
    """Accept text inline, from a file, or from stdin via an explicit '-'.

    Reading stdin only on an explicit '-' matters: inferring it from
    ``isatty()`` makes every optional text argument hang when the CLI runs from
    a script or a pipeline.
    """
    if file:
        return Path(file).read_text(encoding="utf-8")
    if value == "-":
        return sys.stdin.read()
    return value or ""


def _kv_pairs(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--var 는 key=value 형식이어야 합니다: {item}")
        k, v = item.split("=", 1)
        out[k.strip()] = v
    return out


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_init(args, j: Jarvis) -> int:
    profile = j.init_project(
        args.project,
        template=args.template,
        description=args.description or "",
        stack=args.stack or [],
    )
    if args.json:
        _out({"project": args.project, "profile": profile.to_dict()}, True)
        return 0
    print(f"프로젝트 '{args.project}' 준비 완료 (템플릿: {profile.template})")
    print(f"  홈: {j.store.scope_dir(args.project)}")
    print(f"  카테고리: {', '.join(profile.category_names())}")
    print(f"  폴백 카테고리: {profile.fallback_category()}")
    return 0


def cmd_project_list(args, j: Jarvis) -> int:
    rows = j.projects()
    if args.json:
        _out(rows, True)
        return 0
    view = [
        {
            "project": r["project"],
            "template": r["template"],
            "nodes": r["nodes"],
            "cache": r["cache_entries"],
            "categories": ",".join(r["categories"]),
        }
        for r in rows
    ]
    print(
        _table(
            view,
            [
                ("project", "프로젝트"),
                ("template", "템플릿"),
                ("nodes", "노드"),
                ("cache", "캐시"),
                ("categories", "카테고리"),
            ],
        )
    )
    return 0


def cmd_project_profile(args, j: Jarvis) -> int:
    if args.set_template:
        profile = j.apply_template(args.project, args.set_template)
        # Confirmations go to stderr so `--json` stdout stays parseable.
        print(
            f"'{args.project}' 프로파일을 '{args.set_template}' 템플릿으로 교체했습니다.",
            file=sys.stderr if args.json else sys.stdout,
        )
    else:
        profile = j.profile(args.project)
    if args.json:
        _out(profile.to_dict(), True)
        return 0
    print(f"프로젝트: {args.project} · 템플릿: {profile.template}")
    if profile.description:
        print(f"설명: {profile.description}")
    print(f"폴백: {profile.fallback_category()}")
    print()
    print(
        _table(
            [
                {
                    "name": c.name,
                    "priority": c.priority,
                    "keep": c.keep,
                    "cumulative": "예" if c.cumulative else "아니오",
                    "description": c.description,
                }
                for c in profile.categories
            ],
            [
                ("name", "카테고리"),
                ("priority", "우선"),
                ("keep", "보관"),
                ("cumulative", "누적"),
                ("description", "설명"),
            ],
        )
    )
    print(f"\n프로파일 파일: {j.store.scope_dir(args.project) / 'profile.yaml'} (직접 편집 가능)")
    return 0


def cmd_project_templates(args, j: Jarvis) -> int:
    rows = j.templates()
    if args.json:
        _out(rows, True)
        return 0
    print(
        _table(
            [
                {
                    "template": r["template"],
                    "categories": ", ".join(r["categories"]),
                    "description": r["description"],
                }
                for r in rows
            ],
            [("template", "템플릿"), ("description", "설명"), ("categories", "카테고리")],
        )
    )
    return 0


def cmd_project_delete(args, j: Jarvis) -> int:
    if not args.yes:
        print(f"'{args.project}' 의 모든 메모리·프롬프트·세션이 삭제됩니다. --yes 로 확인하세요.")
        return 1
    ok = j.delete_project(args.project)
    print("삭제했습니다." if ok else "해당 프로젝트가 없습니다.")
    return 0 if ok else 1


# ----- prompts -------------------------------------------------------------
def cmd_prompt_save(args, j: Jarvis) -> int:
    text = _read_text_arg(args.template, args.file)
    if not text.strip():
        raise SystemExit("프롬프트 본문이 비어 있습니다 (--file 또는 - 로 stdin 사용)")
    node = j.save_prompt(
        args.project,
        args.name,
        text,
        title=args.title or "",
        description=args.description or "",
        tags=args.tag or [],
    )
    vars_ = node.extra.get("vars", [])
    if args.json:
        _out({"uri": str(node.uri), "version": node.extra.get("version"), "vars": vars_}, True)
        return 0
    print(f"저장: {node.uri} (v{node.extra.get('version')})")
    print(f"  변수: {', '.join(vars_) if vars_ else '(없음)'} · {estimate_tokens(text)} 토큰")
    return 0


def cmd_prompt_list(args, j: Jarvis) -> int:
    rows = j.list_prompts(args.project)
    if args.json:
        _out(rows, True)
        return 0
    print(
        _table(
            [
                {
                    "name": r["name"],
                    "scope": r["scope"],
                    "v": r["version"],
                    "uses": r["uses"],
                    "tokens": r["tokens"],
                    "vars": ",".join(r["vars"]),
                    "title": r["title"],
                }
                for r in rows
            ],
            [
                ("name", "이름"),
                ("scope", "스코프"),
                ("v", "v"),
                ("uses", "사용"),
                ("tokens", "토큰"),
                ("vars", "변수"),
                ("title", "제목"),
            ],
        )
    )
    return 0


def cmd_prompt_show(args, j: Jarvis) -> int:
    node = j.get_prompt(args.project, args.name)
    if node is None:
        print("없습니다.")
        return 1
    if args.json:
        _out({"uri": str(node.uri), "body": node.body, "extra": node.extra}, True)
        return 0
    print(node.body)
    return 0


def cmd_prompt_render(args, j: Jarvis) -> int:
    res = j.render_prompt(args.project, args.name, _kv_pairs(args.var), strict=args.strict)
    if args.json:
        _out({"text": res.text, "tokens": res.tokens, "missing": res.missing}, True)
        return 0
    print(res.text)
    if res.missing:
        print(f"\n[미채움 변수: {', '.join(res.missing)}]", file=sys.stderr)
    return 0


def cmd_prompt_versions(args, j: Jarvis) -> int:
    rows = j.prompt_versions(args.project, args.name)
    if args.json:
        _out(rows, True)
        return 0
    print(_table(rows, [("version", "버전"), ("tokens", "토큰"), ("updated", "시각")]))
    return 0


def cmd_prompt_rollback(args, j: Jarvis) -> int:
    node = j.rollback_prompt(args.project, args.name, args.version)
    if node is None:
        print("해당 버전이 없습니다.")
        return 1
    print(f"되돌렸습니다: {node.uri} (v{node.extra.get('version')})")
    return 0


def cmd_prompt_delete(args, j: Jarvis) -> int:
    ok = j.delete_prompt(args.project, args.name)
    print("삭제했습니다." if ok else "없습니다.")
    return 0 if ok else 1


# ----- memory --------------------------------------------------------------
def cmd_mem_add(args, j: Jarvis) -> int:
    detail = _read_text_arg(args.detail, args.file)
    uri = j.remember(
        args.project,
        args.category,
        args.title,
        args.statement,
        detail=detail,
        tags=args.tag or None,
        confidence=args.confidence,
    )
    print(f"기록: {uri}")
    return 0


def cmd_mem_list(args, j: Jarvis) -> int:
    rows = j.memories(args.project, args.category or "", limit=args.limit)
    if args.json:
        _out(rows, True)
        return 0
    print(
        _table(
            [
                {
                    "category": r["category"],
                    "title": r["title"],
                    "conf": r["confidence"],
                    "hits": r["hits"],
                    "l0": r["tokens"]["l0"],
                    "l2": r["tokens"]["l2"],
                    "updated": r["updated"][:10],
                }
                for r in rows
            ],
            [
                ("category", "카테고리"),
                ("title", "제목"),
                ("conf", "신뢰"),
                ("hits", "사용"),
                ("l0", "L0"),
                ("l2", "L2"),
                ("updated", "수정"),
            ],
        )
    )
    return 0


def cmd_mem_forget(args, j: Jarvis) -> int:
    ok = j.forget(args.uri, archive=not args.purge)
    if ok:
        print("보관함으로 이동했습니다." if not args.purge else "삭제했습니다.")
    else:
        print("대상을 찾지 못했습니다.")
    return 0 if ok else 1


def cmd_mem_feedback(args, j: Jarvis) -> int:
    node = j.feedback(args.project, args.uri, helpful=not args.bad, note=args.note or "")
    if node is None:
        print("대상을 찾지 못했습니다.")
        return 1
    print(f"신뢰도 {node.confidence:.2f} 로 갱신했습니다.")
    return 0


def cmd_resource_add(args, j: Jarvis) -> int:
    text = _read_text_arg(args.text, args.file)
    if not text.strip():
        raise SystemExit("본문이 비어 있습니다")
    node = j.add_resource(args.project, args.name, text, title=args.title or "")
    print(f"등록: {node.uri}")
    print(f"  L0 {estimate_tokens(node.abstract)} / L1 {estimate_tokens(node.overview)} / L2 {estimate_tokens(node.body)} 토큰")
    return 0


# ----- ask / commit --------------------------------------------------------
def cmd_ask(args, j: Jarvis) -> int:
    question = _read_text_arg(args.question, args.file)
    if not question.strip():
        raise SystemExit("질문이 비어 있습니다")
    prepared = j.prepare(
        args.project,
        question,
        prompt=args.prompt or "",
        values=_kv_pairs(args.var),
        use_cache=not args.no_cache,
        max_tier=args.max_tier,
    )
    if args.json:
        _out(prepared.to_dict(), True)
        return 0

    if prepared.cache_hit:
        h = prepared.cache_hit
        print(f"[캐시 적중 · {h.kind} · 유사도 {h.similarity:.3f} · {h.tokens_saved} 토큰 절약]")
        print(f"[원 질문: {h.question[:100]}]")
        print(f"[기록 시각: {h.created}]\n")
        print(h.answer)
        return 0

    pk = prepared.packed
    if args.context_only:
        print(prepared.context)
        return 0
    print("=== SYSTEM ===")
    print(prepared.system)
    print("\n=== USER ===")
    print(prepared.user)
    if pk is not None:
        print("\n=== 예산 ===", file=sys.stderr)
        print(
            f"사용 {pk.tokens} / 동일 항목 전체 로드 {pk.baseline_tokens} / "
            f"관련 전량 덤프 {pk.dump_tokens} 토큰 · 절감 {pk.saved_ratio:.1%}",
            file=sys.stderr,
        )
        for i in pk.items:
            print(f"  L{i.tier} {i.tokens:>5}t  {i.uri}", file=sys.stderr)
    for r in prepared.references:
        print(f"  [참고] {r['uri']} (유사도 {r['score']}, 전체 {r['tokens_if_loaded']}t)", file=sys.stderr)
    return 0


def cmd_commit(args, j: Jarvis) -> int:
    question = args.question
    answer = _read_text_arg(args.answer, args.file)
    if not answer.strip():
        raise SystemExit("답변이 비어 있습니다 (--file 또는 - 로 stdin 사용)")
    res = j.commit(
        args.project,
        question,
        answer,
        model=args.model or "",
        tokens_in=args.tokens_in,
        tokens_out=args.tokens_out,
        outcome=args.outcome or "",
        distill=not args.no_distill,
    )
    if args.json:
        _out(res, True)
        return 0
    print(f"세션 기록: {res['session']}")
    d = res.get("distill")
    if d:
        print(f"증류: 신규 {len(d['created'])} · 병합 {len(d['merged'])} · 보관 {len(d['archived'])}")
        for uri in d["created"] + d["merged"]:
            print(f"  → {uri}")
        for note in d.get("notes", []):
            print(f"  ! {note}")
    return 0


def cmd_distill(args, j: Jarvis) -> int:
    rep = j.distill(args.project, limit=args.limit)
    if args.json:
        _out(rep.to_dict(), True)
        return 0
    print(f"세션 {rep.sessions}건 처리 (LLM: {'사용' if rep.used_llm else '미사용'})")
    print(f"신규 {len(rep.created)} · 병합 {len(rep.merged)} · 상충 {len(rep.conflicts)} · 보관 {len(rep.archived)} · 감쇠 {rep.decayed}")
    for uri in rep.created:
        print(f"  + {uri}")
    for uri in rep.merged:
        print(f"  ~ {uri}")
    for uri in rep.conflicts:
        print(f"  ! 상충 확인 필요: {uri}")
    for note in rep.notes:
        print(f"  ! {note}")
    return 0


# ----- browsing ------------------------------------------------------------
def cmd_ls(args, j: Jarvis) -> int:
    data = j.ls(args.uri)
    if args.json:
        _out(data, True)
        return 0
    print(data["uri"])
    for d in data["dirs"]:
        print(f"  📁 {d['name']:<24} {d['children']:>4}개  {d['abstract'][:60]}")
    for n in data["nodes"]:
        print(f"  📄 {n['name']:<24} L0={n['tokens']['l0']:>4} L2={n['tokens']['l2']:>5} conf={n['confidence']}  {n['title'][:40]}")
    if not data["dirs"] and not data["nodes"]:
        print("  (비어 있음)")
    return 0


def cmd_tree(args, j: Jarvis) -> int:
    data = j.tree(args.uri, depth=args.depth)
    if args.json:
        _out(data, True)
        return 0

    def walk(node: dict, indent: str = "") -> None:
        print(f"{indent}{node['name']}/")
        for n in node.get("nodes", []):
            print(f"{indent}  {n['name']}  ({n['tokens']['l0']}/{n['tokens']['l2']}t)")
        for d in node.get("dirs", []):
            if d.get("truncated"):
                print(f"{indent}  {d['name']}/ ...")
            else:
                walk(d, indent + "  ")

    walk(data)
    return 0


def cmd_find(args, j: Jarvis) -> int:
    data = j.find(args.query, args.project, kinds=args.kind or None, limit=args.limit)
    if args.json:
        _out(data, True)
        return 0
    for r in data["results"]:
        print(f"{r['score']:.4f}  {r['kind']:<8} {r['uri']}")
        print(f"        {r['abstract'][:110]}")
    if args.trace:
        print("\n--- 검색 경로 ---")
        for step in data["trace"]:
            print(f"  {step}")
    return 0


def cmd_grep(args, j: Jarvis) -> int:
    rows = j.grep(args.term, args.uri, limit=args.limit)
    if args.json:
        _out(rows, True)
        return 0
    for r in rows:
        print(f"{r['uri']}:{r['line']}: {r['text']}")
    if not rows:
        print("(일치 없음)")
    return 0


def cmd_read(args, j: Jarvis) -> int:
    data = j.read(args.uri, tier=args.tier)
    if data is None:
        print("없습니다.")
        return 1
    if args.json:
        _out(data, True)
        return 0
    print(f"# {data['title']}  [{data['kind']}/{data['category']}] conf={data['confidence']:.2f} hits={data['hits']}")
    if data["sources"]:
        print(f"출처: {', '.join(data['sources'][:5])}")
    print()
    print(data["text"])
    return 0


# ----- accounting ----------------------------------------------------------
def cmd_report(args, j: Jarvis) -> int:
    rep = j.report(args.project, days=args.days)
    if args.json:
        _out(rep.to_dict(), True)
        return 0
    print(format_report(rep))
    return 0


def cmd_stats(args, j: Jarvis) -> int:
    data = j.stats(args.project)
    if args.json:
        _out(data, True)
        return 0
    rows = []
    for scope, s in data.items():
        rows.append(
            {
                "scope": scope,
                "template": s["profile"],
                "nodes": s["total_nodes"],
                "l0": s["tokens_l0"],
                "l2": s["tokens_l2"],
                "cache": s["cache_entries"],
                "hits": s["cache_hits"],
            }
        )
    print(
        _table(
            rows,
            [
                ("scope", "스코프"),
                ("template", "템플릿"),
                ("nodes", "노드"),
                ("l0", "L0합"),
                ("l2", "L2합"),
                ("cache", "캐시"),
                ("hits", "적중"),
            ],
        )
    )
    return 0


def cmd_cache(args, j: Jarvis) -> int:
    if args.clear:
        n = j.cache_clear(args.project)
        print(f"{n}건 삭제했습니다.")
        return 0
    rows = j.cache_list(args.project, limit=args.limit)
    if args.json:
        _out(rows, True)
        return 0
    print(
        _table(
            rows,
            [("id", "id"), ("hits", "적중"), ("tokens", "토큰"), ("question", "질문")],
        )
    )
    return 0


def cmd_sessions(args, j: Jarvis) -> int:
    rows = j.recent_sessions(args.project, limit=args.limit)
    if args.json:
        _out(rows, True)
        return 0
    for r in rows:
        print(f"{r['created'][:19]}  {r['tokens']:>5}t  {r['uri']}")
        print(f"    {r['question'][:110]}")
    if not rows:
        print("(없음)")
    return 0


def cmd_reindex(args, j: Jarvis) -> int:
    counts = j.reindex(args.project)
    print(f"재색인 완료: 노드 {counts['nodes']} · 스코프 {counts['scopes']}")
    return 0


def cmd_config(args, j: Jarvis) -> int:
    cfg = j.config
    if args.set:
        for item in args.set:
            if "=" not in item:
                raise SystemExit(f"key=value 형식이어야 합니다: {item}")
            key, value = item.split("=", 1)
            _config_set(cfg, key.strip(), value.strip())
        cfg.save()
        print(
            f"저장했습니다: {cfg.config_path}",
            file=sys.stderr if args.json else sys.stdout,
        )
    if args.json:
        _out(cfg.to_dict(), True)
        return 0
    import yaml as _yaml

    print(f"# {cfg.config_path}")
    print(_yaml.safe_dump(cfg.to_dict(), allow_unicode=True, sort_keys=False))
    return 0


def _config_set(cfg: Config, key: str, value: str) -> None:
    parts = key.split(".")
    target: Any = cfg
    for part in parts[:-1]:
        if not hasattr(target, part):
            raise SystemExit(f"알 수 없는 설정 키: {key}")
        target = getattr(target, part)
    leaf = parts[-1]
    if not hasattr(target, leaf):
        raise SystemExit(f"알 수 없는 설정 키: {key}")
    current = getattr(target, leaf)
    if isinstance(current, bool):
        cast: Any = value.lower() in ("1", "true", "yes", "y", "on")
    elif isinstance(current, int):
        cast = int(value)
    elif isinstance(current, float):
        cast = float(value)
    else:
        cast = value
    setattr(target, leaf, cast)




# ----- server operations ---------------------------------------------------
def cmd_key_create(args, j: Jarvis) -> int:
    from .auth import KeyStore

    store = KeyStore(j.store.db)
    first = not store.any_active()
    kid, raw = store.create(args.name, args.project or ["*"])
    if args.json:
        _out({"id": kid, "name": args.name, "key": raw}, True)
        return 0
    print(f"발급: {kid} ({args.name})")
    print(f"\n  {raw}\n")
    print("이 값은 다시 볼 수 없습니다. 지금 저장하세요.")
    if first:
        print("\n첫 키를 만들었으므로 이제부터 서버 전체가 인증을 요구합니다.")
    return 0


def cmd_key_list(args, j: Jarvis) -> int:
    from .auth import KeyStore

    rows = KeyStore(j.store.db).list()
    if args.json:
        _out(rows, True)
        return 0
    print(
        _table(
            [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "projects": r["projects"],
                    "calls": r["calls"],
                    "last": (r["last_used"] or "-")[:19],
                    "state": "폐기" if r["revoked"] else "활성",
                }
                for r in rows
            ],
            [
                ("id", "id"),
                ("name", "이름"),
                ("projects", "프로젝트"),
                ("calls", "호출"),
                ("last", "최근 사용"),
                ("state", "상태"),
            ],
        )
    )
    return 0


def cmd_key_revoke(args, j: Jarvis) -> int:
    from .auth import KeyStore

    ok = KeyStore(j.store.db).revoke(args.key_id)
    print("폐기했습니다." if ok else "해당 키가 없거나 이미 폐기되었습니다.")
    return 0 if ok else 1


def _git_remote(path: str = ".") -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def cmd_link(args, j: Jarvis) -> int:
    """Bind this checkout to a project so any agent here resolves to it."""
    target = str(Path(args.path or ".").resolve())
    repo = args.repo or _git_remote(target)
    project = args.project or (
        _slug_from_repo(repo) if repo else Path(target).name
    )
    j.init_project(project, template=args.template)
    bound = []
    if repo:
        j.bind_alias(repo, project, "repo")
        bound.append(f"repo {repo}")
    j.bind_alias(target, project, "path")
    bound.append(f"path {target}")
    if args.json:
        _out({"project": project, "bound": bound}, True)
        return 0
    print(f"'{project}' 에 연결했습니다.")
    for b in bound:
        print(f"  {b}")
    if not repo:
        print("\n(git remote 를 찾지 못해 경로로만 연결했습니다. 다른 머신에서도 같은")
        print(" 프로젝트로 붙이려면 --repo 로 remote 를 지정하세요.)")
    return 0


def _slug_from_repo(repo: str) -> str:
    from .service import _project_from_alias

    return _project_from_alias(repo) or "project"


def cmd_agent_config(args, j: Jarvis) -> int:
    from .connect import build, instruction_file

    try:
        conn = build(args.client, args.url, args.project or "", args.key or "")
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _out(conn.to_dict(), True)
        return 0
    print(f"# 1) MCP 연결 — {conn.where}\n")
    print(conn.setup)
    print(f"\n# 2) 에이전트 지시문 — {instruction_file(args.client)} 에 추가\n")
    print(conn.instructions)
    if not conn.has_key:
        print("\n(API 키 없이 생성했습니다. 서버가 인증을 요구하면 --key 를 주세요.)")
    return 0


# ----- observability ------------------------------------------------------
def cmd_traces(args, j: Jarvis) -> int:
    rows = j.traces(args.project or "", limit=args.limit, min_latency=args.slower_than)
    if args.json:
        _out(rows, True)
        return 0
    view = [
        {
            "id": r["id"],
            "time": (r["started"] or "")[5:19].replace("T", " "),
            "project": r["scope"],
            "result": f"재사용 {r['cache_hit']}" if r["cache_hit"] else "생성",
            "ctx": f"{r['latency_ms']}ms",
            "total": f"{r['total_ms'] or r['latency_ms']}ms",
            "score": "-" if r["avg_score"] is None else f"{r['avg_score']:.2f}",
            "question": (r["input"] or "")[:60],
        }
        for r in rows
    ]
    print(
        _table(
            view,
            [
                ("time", "시각"),
                ("project", "프로젝트"),
                ("result", "결과"),
                ("ctx", "조립"),
                ("total", "전체"),
                ("score", "점수"),
                ("id", "트레이스"),
                ("question", "질문"),
            ],
        )
    )
    return 0


def cmd_trace_show(args, j: Jarvis) -> int:
    t = j.trace(args.trace_id)
    if t is None:
        print("없는 트레이스입니다.")
        return 1
    if args.json:
        _out(t, True)
        return 0
    print(f"{t['id']}  [{t['scope']}] {t['started']}")
    print(
        f"조립 {t['latency_ms']}ms · 전체 {t['total_ms'] or t['latency_ms']}ms · "
        f"{'재사용 ' + t['cache_hit'] if t['cache_hit'] else '생성'}"
        + (f" · agent {t['agent']}" if t["agent"] else "")
    )
    print(f"\n질문: {t['input'][:200]}")
    if t["output"]:
        print(f"답변: {t['output'][:300]}")
    print("\n단계:")
    for o in t["observations"]:
        print(f"  {o['type']:<11} {o['name']:<16} {o['latency_ms']:>6}ms  {(o['output'] or '')[:50]}")
    print("\n사용된 컨텍스트:")
    for c in t["context"]:
        print(f"  L{c['tier']} {c['tokens']:>5}t  {c['score']:.3f}  {c['uri']}")
    if t["scores"]:
        print("\n점수:")
        for sc in t["scores"]:
            print(f"  {sc['name']}={sc['value']} ({sc['source']}) {sc['comment']}")
    return 0


def cmd_score(args, j: Jarvis) -> int:
    try:
        res = j.score(
            args.trace_id,
            name=args.name,
            value=args.value,
            comment=args.comment or "",
        )
    except KeyError as exc:
        print(f"오류: {exc.args[0]}", file=sys.stderr)
        return 1
    if args.json:
        _out(res, True)
        return 0
    print(f"{args.name}={args.value} 기록 · 메모리 {len(res['memories_adjusted'])}건 신뢰도 조정")
    for uri in res["memories_adjusted"]:
        print(f"  → {uri}")
    return 0


def cmd_metrics(args, j: Jarvis) -> int:
    m = j.metrics(args.project or "", days=args.days)
    if args.json:
        _out(m, True)
        return 0
    print(f"프로젝트: {m['scope']} · 최근 {m['days']}일 · 작업 {m['traces']}건 (오류 {m['errors']})")
    a, c = m["answer_ms"], m["context_ms"]
    print(f"\n응답 시간   p50 {a['p50']}ms · p95 {a['p95']}ms")
    print(f"  재사용    p50 {a['p50_reused']}ms")
    print(f"  생성      p50 {a['p50_generated']}ms")
    print(f"컨텍스트 조립 p50 {c['p50']}ms · p95 {c['p95']}ms")
    oc = m.get("outcomes") or {}
    judged = sum(oc.values())
    if judged:
        again = oc.get("reworked", 0) + oc.get("repeated", 0)
        print(
            f"\n한 번에 해결 {m['first_try_rate'] * 100:.0f}%"
            f"  (다시 요청 {again} / 판정 {judged}건)"
        )
        print(
            "  판정 근거: "
            + ", ".join(f"{REASON_OUTCOME.get(k, k)} {v}" for k, v in sorted(oc.items()))
        )
    else:
        print("\n한 번에 해결: 판정 데이터 없음 (다음 요청이 판정 근거입니다)")
    print(f"재사용률 {m['reuse']['rate'] * 100:.1f}% ({m['reuse']['hits']}/{m['traces']})")
    if m["scores"]:
        print("점수: " + ", ".join(f"{s['name']} {s['avg']:.2f} ({s['count']}건)" for s in m["scores"]))
    else:
        print("점수: 아직 없음 — jv score 또는 jarvis_score 로 남기면 메모리 품질이 개선됩니다")
    if m["steps"]:
        print("\n단계별 평균:")
        for st in m["steps"]:
            print(f"  {st['type']:<11} {st['avg_ms']:>6}ms  (최대 {st['max_ms']}ms, {st['count']}회)")
    print(f"\n토큰: 입력 {m['tokens']['in']:,} · 출력 {m['tokens']['out']:,}")
    return 0


def cmd_impact(args, j: Jarvis) -> int:
    rows = j.memory_impact(args.project, limit=args.limit)
    if args.json:
        _out(rows, True)
        return 0
    print(
        _table(
            [
                {
                    "uri": r["uri"].split("/", 4)[-1],
                    "uses": r["uses"],
                    "scored": r["scored"],
                    "avg": "미검증" if r["avg_score"] is None else f"{r['avg_score']:.2f}",
                    "tokens": r["avg_tokens"],
                }
                for r in rows
            ],
            [
                ("uri", "컨텍스트"),
                ("uses", "사용"),
                ("scored", "점수받음"),
                ("avg", "평균 점수"),
                ("tokens", "평균 토큰"),
            ],
        )
    )
    return 0


def cmd_agents(args, j: Jarvis) -> int:
    rows = j.agents()
    if args.json:
        _out(rows, True)
        return 0
    print(
        _table(
            [
                {
                    "name": r["name"],
                    "projects": ", ".join(r["projects"]),
                    "calls": r["calls"],
                    "last": (r["last_seen"] or "")[:19].replace("T", " "),
                }
                for r in rows
            ],
            [("name", "에이전트"), ("projects", "프로젝트"), ("calls", "호출"), ("last", "최근")],
        )
    )
    return 0




# ----- curation -----------------------------------------------------------
# How an answer landed, inferred from the request that followed it.
REASON_OUTCOME = {
    "reworked": "다시 요청됨",
    "repeated": "같은 요청 반복",
    "moved_on": "넘어감",
}

REASON_LABEL = {
    "conflict": ("상충", "같은 주제에 반대되는 내용이 들어왔습니다"),
    "harmful": ("나쁜 결과", "이 메모리가 들어간 작업 평가가 낮습니다"),
    "unproven": ("미검증", "여러 번 쓰였지만 평가가 없습니다"),
    "unconfirmed": ("확인 대기", "에이전트가 기록했고 아직 사람이 보지 않았습니다"),
    "fading": ("잊히는 중", "오래 쓰이지 않아 신뢰도가 떨어졌습니다"),
}


def cmd_review(args, j: Jarvis) -> int:
    """What did my agents write down, and is it right?"""
    if args.summary or not args.project:
        summary = j.review_summary(args.project or "", args.all)
        if args.json:
            _out(summary, True)
            return 0
        if not summary["total"]:
            print("확인할 것이 없습니다. 지식 저장소는 스스로 정리되고 있습니다.")
            if not args.all:
                print("(에이전트가 기록한 것을 모두 훑어보려면 --all)")
            return 0
        print(f"확인이 필요한 항목 {summary['total']}건\n")
        for reason, count in sorted(
            summary["by_reason"].items(), key=lambda kv: -kv[1]
        ):
            label, why = REASON_LABEL.get(reason, (reason, ""))
            print(f"  {label:<10} {count:>3}건  {why}")
        print()
        rows = [
            {"project": name, "items": v["items"]}
            for name, v in summary["projects"].items()
            if v["items"]
        ]
        print(_table(rows, [("project", "프로젝트"), ("items", "건수")]))
        print("\n자세히: jv review -p <프로젝트>")
        return 0

    queue = j.review_queue(args.project, limit=args.limit, include_unconfirmed=args.all)
    if args.json:
        _out(queue, True)
        return 0
    if not queue:
        print(f"'{args.project}' 에 확인할 것이 없습니다.")
        if not args.all:
            print("(에이전트가 기록한 것을 모두 훑어보려면 --all)")
        return 0

    print(f"'{args.project}' 확인 필요 {len(queue)}건 — 위쪽이 더 시급합니다\n")
    for item in queue:
        labels = " ".join(REASON_LABEL.get(r, (r, ""))[0] for r in item["reasons"])
        print(f"[{labels}] {item['category']}/{item['title']}")
        print(f"  {item['abstract'][:110]}")
        meta = (
            f"  출처={'직접 작성' if item['origin'] == 'manual' else '에이전트'}"
            f" 신뢰={item['confidence']} 사용={item['uses']}회"
        )
        if item["avg_score"] is not None:
            meta += f" 평균점수={item['avg_score']}"
        print(meta)
        if item["conflict"]:
            c = item["conflict"]
            print(f"  기존: {c['existing'][:80]}")
            print(f"  유입: {c['incoming'][:80]}")
            if c.get("other"):
                print(f"  상대: {c['other']}")
        print(f"  {item['uri']}")
        print()
    print("확인: jv mem confirm <uri>   |   수정: jv mem edit <uri> --statement '...'")
    print("보관: jv mem forget <uri>    |   파일을 직접 편집한 뒤 jv reindex 도 가능합니다")
    return 0


def cmd_mem_confirm(args, j: Jarvis) -> int:
    try:
        res = j.confirm_memory(args.uri, args.confidence)
    except KeyError as exc:
        print(f"오류: {exc.args[0]}", file=sys.stderr)
        return 1
    print(f"확인했습니다. 신뢰도 {res['confidence']:.2f}")
    return 0


def cmd_mem_edit(args, j: Jarvis) -> int:
    body = _read_text_arg(args.body, args.file) if (args.body or args.file) else None
    try:
        res = j.edit_memory(
            args.uri,
            title=args.title,
            statement=args.statement,
            body=body,
            category=args.category,
            tags=args.tag,
            confidence=args.confidence,
        )
    except KeyError as exc:
        print(f"오류: {exc.args[0]}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    print(f"수정했습니다: {res['uri']}")
    if res["moved_from"]:
        print(f"  (이동: {res['moved_from']})")
    return 0


def cmd_mem_show(args, j: Jarvis) -> int:
    d = j.memory_detail(args.uri)
    if d is None:
        print("없는 메모리입니다.")
        return 1
    if args.json:
        _out(d, True)
        return 0
    print(f"# {d['title']}   [{d['category']}]")
    print(
        f"출처={'직접 작성' if d['origin'] == 'manual' else '에이전트'} "
        f"확인={'예' if d['reviewed'] else '아니오'} 신뢰={d['confidence']} "
        f"사용={d['hits']}회 · 작업 {d['impact']['uses']}건에 포함"
    )
    if d["reasons"]:
        labels = ", ".join(REASON_LABEL.get(r, (r, ""))[0] for r in d["reasons"])
        print(f"확인 필요: {labels}")
    if d["conflict"]:
        print(f"\n기존: {d['conflict']['existing']}")
        print(f"유입: {d['conflict']['incoming']}")
        if d["conflict"].get("other"):
            print(f"상대 메모리: {d['conflict']['other']}")
    print(f"\n요약(L0): {d['abstract']}")
    print(f"\n본문(L2):\n{d['body']}")
    print(f"\n토큰 L0={d['tokens']['l0']} L1={d['tokens']['l1']} L2={d['tokens']['l2']}")
    print(f"파일: {d['path']}")
    if d["sources"]:
        print("출처 세션: " + ", ".join(d["sources"][-3:]))
    return 0




# ----- you, and coming back to a project ----------------------------------
def cmd_me_add(args, j: Jarvis) -> int:
    statement = _read_text_arg(args.statement, None)
    try:
        uri = j.remember_about_me(
            statement, title=args.title or "", category=args.category
        )
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    print(f"기록: {uri}")
    print("  이 항목은 모든 프로젝트의 컨텍스트에 함께 실립니다.")
    return 0


def cmd_me_list(args, j: Jarvis) -> int:
    rows = j.about_me()
    if args.json:
        _out(rows, True)
        return 0
    if not rows:
        print("전역 선호가 아직 없습니다.")
        print('예: jv me add "답변과 주석은 항상 한글로 작성한다"')
        return 0
    print(
        _table(
            [
                {
                    "category": r["category"],
                    "statement": r["abstract"][:70],
                    "conf": r["confidence"],
                    "hits": r["hits"],
                }
                for r in rows
            ],
            [("category", "카테고리"), ("statement", "내용"), ("conf", "신뢰"), ("hits", "사용")],
        )
    )
    return 0


def cmd_me_forget(args, j: Jarvis) -> int:
    try:
        ok = j.forget_about_me(args.uri)
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    print("보관함으로 옮겼습니다." if ok else "대상을 찾지 못했습니다.")
    return 0 if ok else 1


def cmd_brief(args, j: Jarvis) -> int:
    b = j.brief(args.project, limit=args.limit)
    if args.json:
        _out(b, True)
        return 0
    t = b["totals"]
    print(f"# {b['project']}  ({b['template']})")
    if b["description"]:
        print(b["description"])
    line = f"메모리 {t['memories']}개 · 확립됨 {t['established']} · 확인 필요 {t['needs_review']}"
    if t.get("disputed"):
        line += f" · 상충 {t['disputed']}"
    print(line)
    if b["warnings"]:
        print("\n## 주의 — 이미 밟은 함정")
        for w in b["warnings"]:
            print(f"  · {w['title']}")
            print(f"    {w['abstract'][:100]}")
    if b["know"]:
        print("\n## 확립된 지식")
        for k in b["know"]:
            print(f"  · [{k['category']}] {k['title']}")
            print(f"    {k['abstract'][:100]}")
    if b["unresolved"]:
        print("\n## 미해결 — 결정이 필요합니다")
        for u in b["unresolved"]:
            labels = ", ".join(REASON_LABEL.get(r, (r, ""))[0] for r in u["reasons"])
            print(f"  · [{labels}] {u['title']}")
            print(f"    {u['uri']}")
    if b["prompts"]:
        print("\n## 저장된 프롬프트")
        for pr in b["prompts"]:
            print(f"  · {pr['name']}  ({pr['uses']}회 사용) {pr['description'][:50]}")
    if b["recent_sessions"]:
        print("\n## 최근 작업")
        for sess in b["recent_sessions"]:
            print(f"  · {sess['created'][:10]}  {sess['question'][:70]}")
    return 0


def cmd_history(args, j: Jarvis) -> int:
    """이전 작업을 세션 단위로 되짚습니다."""
    sessions = j.work_sessions(args.project or "", limit=args.limit)
    if args.json:
        _out(sessions, True)
        return 0
    if not sessions:
        print("아직 작업 기록이 없습니다.")
        return 0
    for sess in sessions:
        head = f"{sess['started'][:16].replace('T', ' ')}  {sess['agent'] or '(에이전트 미표기)'}"
        meta = f"{sess['traces']}건"
        if sess["reused"]:
            meta += f" (재사용 {sess['reused']})"
        if sess["avg_score"] is not None:
            meta += f" · 평균 {sess['avg_score']}"
        print(f"\n■ {head}  —  {meta}")
        for w in sess["work"]:
            mark = "↻" if w["reused"] else " "
            verdict = REASON_OUTCOME.get(w.get("outcome") or "", "")
            tail = f"  ({verdict})" if verdict else ""
            print(f"  {mark} Q: {(w['question'] or '')[:64]}{tail}")
            if w["answer"] and args.verbose:
                print(f"    A: {w['answer'][:150]}")
    return 0


def cmd_digest(args, j: Jarvis) -> int:
    d = j.digest(days=args.days)
    if args.json:
        _out(d, True)
        return 0
    print(f"최근 {d['days']}일 · 전역 선호 {d['about_me']}개 · 확인 필요 총 {d['review_total']}건\n")
    if not d["projects"]:
        print("프로젝트가 없습니다.")
        return 0
    print(
        _table(
            [
                {
                    "project": e["project"],
                    "traces": e["traces"],
                    "reuse": f"{e['reuse_rate'] * 100:.0f}%",
                    "p50": f"{e['answer_p50_ms']}ms",
                    "new": e["new_memories"],
                    "review": e["needs_review"],
                    "score": (
                        f"{e['scores'][0]['avg']:.2f}" if e["scores"] else "-"
                    ),
                }
                for e in d["projects"]
            ],
            [
                ("project", "프로젝트"),
                ("traces", "작업"),
                ("reuse", "재사용"),
                ("p50", "응답 p50"),
                ("new", "새 메모리"),
                ("review", "확인 필요"),
                ("score", "점수"),
            ],
        )
    )
    if d["review_total"]:
        print("\n확인: jv review")
    return 0


def cmd_maintain(args, j: Jarvis) -> int:
    res = j.maintain()
    total = {"created": 0, "merged": 0, "archived": 0, "sessions": 0}
    for r in res["projects"]:
        for k in total:
            total[k] += len(r[k]) if isinstance(r.get(k), list) else r.get(k, 0)
    if args.json:
        _out(res, True)
        return 0
    print(
        f"세션 {total['sessions']}건 증류 · 신규 {total['created']} · 병합 {total['merged']}"
        f" · 보관 {total['archived']}"
    )
    return 0


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jv",
        description="MyViking — 프로젝트별 자가학습 컨텍스트 데이터베이스",
    )
    p.add_argument("--version", action="version", version=f"my-viking {__version__}")
    p.add_argument("--home", help="컨텍스트 저장 위치 (기본 ~/.jarvis, 환경변수 JARVIS_HOME)")
    p.add_argument("--json", action="store_true", help="JSON 으로 출력")
    sub = p.add_subparsers(dest="cmd", required=True)

    def proj(sp: Any, required: bool = True) -> None:
        sp.add_argument("-p", "--project", required=required, help="프로젝트 이름")

    # init
    sp = sub.add_parser("init", help="프로젝트 생성 및 메모리 프로파일 지정")
    sp.add_argument("project")
    sp.add_argument("-t", "--template", default="default", choices=templates())
    sp.add_argument("-d", "--description")
    sp.add_argument("--stack", nargs="*")
    sp.set_defaults(func=cmd_init)

    # project
    sp = sub.add_parser("project", help="프로젝트 관리")
    psub = sp.add_subparsers(dest="sub", required=True)
    s = psub.add_parser("list", help="프로젝트 목록")
    s.set_defaults(func=cmd_project_list)
    s = psub.add_parser("profile", help="메모리 프로파일 보기/교체")
    proj(s)
    s.add_argument("--set-template", choices=templates())
    s.set_defaults(func=cmd_project_profile)
    s = psub.add_parser("templates", help="사용 가능한 프로파일 템플릿")
    s.set_defaults(func=cmd_project_templates)
    s = psub.add_parser("delete", help="프로젝트 삭제")
    proj(s)
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_project_delete)

    # prompt
    sp = sub.add_parser("prompt", help="프롬프트 라이브러리")
    psub = sp.add_subparsers(dest="sub", required=True)
    s = psub.add_parser("save", help="프롬프트 저장 (기존 버전은 자동 보관)")
    proj(s)
    s.add_argument("name")
    s.add_argument("template", nargs="?", help="본문 ('-' 는 stdin)")
    s.add_argument("-f", "--file")
    s.add_argument("--title")
    s.add_argument("--description")
    s.add_argument("--tag", action="append")
    s.set_defaults(func=cmd_prompt_save)
    s = psub.add_parser("list", help="프롬프트 목록과 사용 통계")
    proj(s)
    s.set_defaults(func=cmd_prompt_list)
    s = psub.add_parser("show", help="프롬프트 본문 출력")
    proj(s)
    s.add_argument("name")
    s.set_defaults(func=cmd_prompt_show)
    s = psub.add_parser("render", help="변수 채워 렌더링")
    proj(s)
    s.add_argument("name")
    s.add_argument("--var", action="append", help="key=value")
    s.add_argument("--strict", action="store_true", help="변수 누락 시 실패")
    s.set_defaults(func=cmd_prompt_render)
    s = psub.add_parser("versions", help="이전 버전 목록")
    proj(s)
    s.add_argument("name")
    s.set_defaults(func=cmd_prompt_versions)
    s = psub.add_parser("rollback", help="이전 버전으로 되돌리기")
    proj(s)
    s.add_argument("name")
    s.add_argument("version")
    s.set_defaults(func=cmd_prompt_rollback)
    s = psub.add_parser("delete", help="프롬프트 삭제")
    proj(s)
    s.add_argument("name")
    s.set_defaults(func=cmd_prompt_delete)

    # mem
    sp = sub.add_parser("mem", help="메모리 직접 조작")
    msub = sp.add_subparsers(dest="sub", required=True)
    s = msub.add_parser("add", help="메모리 수동 기록")
    proj(s)
    s.add_argument("category")
    s.add_argument("title")
    s.add_argument("statement")
    s.add_argument("--detail")
    s.add_argument("-f", "--file")
    s.add_argument("--tag", action="append")
    s.add_argument("--confidence", type=float, default=0.8)
    s.set_defaults(func=cmd_mem_add)
    s = msub.add_parser("list", help="메모리 목록")
    proj(s)
    s.add_argument("-c", "--category")
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(func=cmd_mem_list)
    s = msub.add_parser("show", help="메모리 상세 (출처·검토 상태·기여도)")
    s.add_argument("uri")
    s.set_defaults(func=cmd_mem_show)
    s = msub.add_parser("confirm", help="이 메모리가 맞다고 확인 (검토 큐에서 제거)")
    s.add_argument("uri")
    s.add_argument("--confidence", type=float)
    s.set_defaults(func=cmd_mem_confirm)
    s = msub.add_parser("edit", help="메모리 수정 (수정하면 확인 처리됩니다)")
    s.add_argument("uri")
    s.add_argument("--title")
    s.add_argument("--statement", help="요약(L0) — 검색에서 먼저 읽히는 문장")
    s.add_argument("--body", help="본문(L2)")
    s.add_argument("-f", "--file", help="본문을 파일에서 읽기")
    s.add_argument("--category")
    s.add_argument("--tag", action="append")
    s.add_argument("--confidence", type=float)
    s.set_defaults(func=cmd_mem_edit)
    s = msub.add_parser("forget", help="메모리 보관/삭제")
    s.add_argument("uri")
    s.add_argument("--purge", action="store_true", help="보관하지 않고 완전 삭제")
    s.set_defaults(func=cmd_mem_forget)
    s = msub.add_parser("feedback", help="메모리 정확도 피드백")
    proj(s)
    s.add_argument("uri")
    s.add_argument("--bad", action="store_true", help="부정확했다고 표시")
    s.add_argument("--note")
    s.set_defaults(func=cmd_mem_feedback)

    # resource
    sp = sub.add_parser("resource", help="참고 자료 등록")
    rsub = sp.add_subparsers(dest="sub", required=True)
    s = rsub.add_parser("add", help="문서를 티어링해 등록")
    proj(s)
    s.add_argument("name")
    s.add_argument("text", nargs="?", help="본문 ('-' 는 stdin)")
    s.add_argument("-f", "--file")
    s.add_argument("--title")
    s.set_defaults(func=cmd_resource_add)

    # ask
    sp = sub.add_parser("ask", help="컨텍스트 조립 (캐시 확인 → 예산 내 패킹)")
    proj(sp)
    sp.add_argument("question", nargs="?", help="질문 ('-' 는 stdin)")
    sp.add_argument("-f", "--file")
    sp.add_argument("--prompt", help="함께 렌더링할 저장된 프롬프트 이름")
    sp.add_argument("--var", action="append", help="프롬프트 변수 key=value")
    sp.add_argument("--no-cache", action="store_true", help="캐시 무시")
    sp.add_argument("--max-tier", type=int, default=2, choices=[0, 1, 2])
    sp.add_argument("--context-only", action="store_true", help="컨텍스트 본문만 출력")
    sp.set_defaults(func=cmd_ask)

    # commit
    sp = sub.add_parser("commit", help="답변 기록 후 메모리로 증류")
    proj(sp)
    sp.add_argument("question")
    sp.add_argument("answer", nargs="?", help="답변 ('-' 는 stdin)")
    sp.add_argument("-f", "--file")
    sp.add_argument("--model")
    sp.add_argument("--tokens-in", type=int, default=0)
    sp.add_argument("--tokens-out", type=int, default=0)
    sp.add_argument("--outcome", help="성공/실패 등 결과 메모")
    sp.add_argument("--no-distill", action="store_true")
    sp.set_defaults(func=cmd_commit)

    sp = sub.add_parser("distill", help="미증류 세션을 메모리로 반영 + 감쇠")
    proj(sp)
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_distill)

    # browsing
    sp = sub.add_parser("ls", help="디렉터리 나열")
    sp.add_argument("uri")
    sp.set_defaults(func=cmd_ls)
    sp = sub.add_parser("tree", help="트리 출력")
    sp.add_argument("uri")
    sp.add_argument("-L", "--depth", type=int, default=3)
    sp.set_defaults(func=cmd_tree)
    sp = sub.add_parser("find", help="의미 검색 (디렉터리 우선 탐색)")
    proj(sp)
    sp.add_argument("query")
    sp.add_argument("--kind", action="append", choices=["memory", "prompt", "session", "resource"])
    sp.add_argument("--limit", type=int, default=15)
    sp.add_argument("--trace", action="store_true", help="검색 경로 표시")
    sp.set_defaults(func=cmd_find)
    sp = sub.add_parser("grep", help="본문 문자열 검색")
    sp.add_argument("term")
    sp.add_argument("--uri")
    sp.add_argument("--limit", type=int, default=30)
    sp.set_defaults(func=cmd_grep)
    sp = sub.add_parser("read", help="노드 읽기 (티어 선택)")
    sp.add_argument("uri")
    sp.add_argument("-t", "--tier", type=int, default=2, choices=[0, 1, 2])
    sp.set_defaults(func=cmd_read)

    # accounting
    sp = sub.add_parser("report", help="토큰 절감 회계")
    proj(sp, required=False)
    sp.add_argument("--days", type=int, default=0)
    sp.set_defaults(func=cmd_report)
    sp = sub.add_parser("stats", help="저장소 규모")
    proj(sp, required=False)
    sp.set_defaults(func=cmd_stats)
    sp = sub.add_parser("cache", help="답변 캐시 조회/삭제")
    proj(sp)
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--clear", action="store_true")
    sp.set_defaults(func=cmd_cache)
    sp = sub.add_parser("sessions", help="최근 세션")
    proj(sp)
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_sessions)
    sp = sub.add_parser("reindex", help="파일에서 색인 재생성")
    proj(sp, required=False)
    sp.set_defaults(func=cmd_reindex)
    sp = sub.add_parser("config", help="설정 보기/변경")
    sp.add_argument("--set", action="append", help="예: llm.provider=anthropic")
    sp.set_defaults(func=cmd_config)

    sp = sub.add_parser("serve", help="서버 실행 (대시보드 + API + 원격 MCP)")
    sp.add_argument("--host", default="127.0.0.1", help="0.0.0.0 으로 열면 외부에서 접속 가능")
    sp.add_argument("--port", type=int, default=8787)
    sp.add_argument(
        "--maintain-every",
        type=float,
        default=6.0,
        metavar="시간",
        help="증류·감쇠를 몇 시간마다 돌릴지 (0=끔, 기본 6)",
    )
    sp.set_defaults(func=cmd_serve)

    # keys
    sp = sub.add_parser("key", help="API 키 관리 (원격 접속용)")
    ksub = sp.add_subparsers(dest="sub", required=True)
    s2 = ksub.add_parser("create", help="키 발급 (첫 키 발급 시 서버가 인증을 요구하게 됩니다)")
    s2.add_argument("name")
    s2.add_argument("--project", action="append", help="접근 허용 프로젝트 (기본 전체)")
    s2.set_defaults(func=cmd_key_create)
    s2 = ksub.add_parser("list", help="키 목록")
    s2.set_defaults(func=cmd_key_list)
    s2 = ksub.add_parser("revoke", help="키 폐기")
    s2.add_argument("key_id")
    s2.set_defaults(func=cmd_key_revoke)

    # link / agent
    sp = sub.add_parser("link", help="현재 저장소를 프로젝트에 연결 (git remote/경로 기준)")
    sp.add_argument("-p", "--project", help="프로젝트 이름 (생략하면 remote 에서 추론)")
    sp.add_argument("--path", help="연결할 경로 (기본 현재 디렉터리)")
    sp.add_argument("--repo", help="git remote URL 직접 지정")
    sp.add_argument("-t", "--template", default="coding", choices=templates())
    sp.set_defaults(func=cmd_link)

    sp = sub.add_parser("agent", help="코딩 에이전트 연동 설정 출력")
    asub = sp.add_subparsers(dest="sub", required=True)
    s2 = asub.add_parser("config", help="클라이언트별 MCP 설정과 지시문 생성")
    s2.add_argument("--client", default="claude-code", choices=list(CLIENTS))
    s2.add_argument("--url", default="http://127.0.0.1:8787", help="서버 주소")
    s2.add_argument("--key", help="API 키 (인증을 켰다면 필요)")
    s2.add_argument("-p", "--project", help="지시문에 넣을 프로젝트 이름")
    s2.set_defaults(func=cmd_agent_config)
    s2 = asub.add_parser("list", help="연결된 에이전트 목록")
    s2.set_defaults(func=cmd_agents)

    # observability
    sp = sub.add_parser("me", help="모든 프로젝트에 적용되는 내 선호")
    esub = sp.add_subparsers(dest="sub", required=True)
    s2 = esub.add_parser("add", help="선호 기록 (예: 답변은 항상 한글로)")
    s2.add_argument("statement")
    s2.add_argument("--title")
    s2.add_argument("-c", "--category", default="preferences")
    s2.set_defaults(func=cmd_me_add)
    s2 = esub.add_parser("list", help="전역 선호 목록")
    s2.set_defaults(func=cmd_me_list)
    s2 = esub.add_parser("forget", help="전역 선호 보관")
    s2.add_argument("uri")
    s2.set_defaults(func=cmd_me_forget)

    sp = sub.add_parser("brief", help="오랜만에 돌아왔을 때 알아야 할 것")
    proj(sp)
    sp.add_argument("--limit", type=int, default=8)
    sp.set_defaults(func=cmd_brief)

    sp = sub.add_parser("history", help="이전 작업을 세션 단위로 되짚기")
    proj(sp, required=False)
    sp.add_argument("--limit", type=int, default=8)
    sp.add_argument("-v", "--verbose", action="store_true", help="답변까지 표시")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("digest", help="전체 프로젝트 요약 (주기적으로 읽기)")
    sp.add_argument("--days", type=int, default=7)
    sp.set_defaults(func=cmd_digest)

    sp = sub.add_parser("maintain", help="증류·감쇠·정리 일괄 실행 (스케줄러용)")
    sp.set_defaults(func=cmd_maintain)

    sp = sub.add_parser("review", help="에이전트가 기록한 것 중 확인이 필요한 항목")
    proj(sp, required=False)
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--summary", action="store_true", help="건수만 요약")
    sp.add_argument(
        "--all",
        action="store_true",
        help="에이전트가 기록했으나 아직 사람이 보지 않은 것까지 포함",
    )
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("traces", help="최근 작업 기록")
    proj(sp, required=False)
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--slower-than", type=int, default=0, help="이 ms 이상만 표시")
    sp.set_defaults(func=cmd_traces)

    sp = sub.add_parser("trace", help="트레이스 상세 (어디서 시간이 갔는지)")
    sp.add_argument("trace_id")
    sp.set_defaults(func=cmd_trace_show)

    sp = sub.add_parser("score", help="작업 결과 평가 → 메모리 신뢰도 반영")
    sp.add_argument("trace_id")
    sp.add_argument("value", type=float, help="0=틀림, 0.5=중립, 1=도움됨")
    sp.add_argument("--name", default="helpfulness")
    sp.add_argument("--comment")
    sp.set_defaults(func=cmd_score)

    sp = sub.add_parser("metrics", help="속도·재사용·품질 지표")
    proj(sp, required=False)
    sp.add_argument("--days", type=int, default=7)
    sp.set_defaults(func=cmd_metrics)

    sp = sub.add_parser("impact", help="어떤 메모리가 좋은 결과에 기여했는지")
    proj(sp)
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_impact)

    return p


def cmd_serve(args, j: Jarvis) -> int:
    from .auth import KeyStore

    try:
        from .server import run
    except ModuleNotFoundError as exc:
        # The core package deliberately depends on PyYAML alone, so a plain
        # install has no web stack. Say which install fixes it.
        print(
            f"서버 의존성이 없습니다 ({exc.name}).\n"
            '  pip install "my-viking[server]"\n'
            "  또는  uv pip install -e \".[all]\"",
            file=sys.stderr,
        )
        return 1

    secured = KeyStore(j.store.db).any_active()
    shown = "localhost" if args.host in ("127.0.0.1", "localhost") else args.host
    print(f"MyViking 서버 http://{shown}:{args.port}")
    print(f"  대시보드   http://{shown}:{args.port}/")
    print(f"  API 문서   http://{shown}:{args.port}/docs")
    print(f"  원격 MCP   http://{shown}:{args.port}/mcp")
    print(f"  인증       {'API 키 필요' if secured else '없음'}")
    if args.host not in ("127.0.0.1", "localhost") and not secured:
        # Opening the port without a key would publish every project's context.
        print(
            "\n경고: 외부 주소로 열었지만 API 키가 없어 누구나 접근할 수 있습니다.\n"
            "       `jv key create <이름>` 으로 키를 먼저 발급하세요."
        )
    if args.maintain_every > 0:
        print(f"  유지보수     {args.maintain_every}시간마다 증류·감쇠")
    print()
    # Under Docker stdout is block-buffered, so without this the banner lands
    # after uvicorn's log lines and reads as if it started twice.
    sys.stdout.flush()
    run(
        host=args.host,
        port=args.port,
        home=args.home,
        maintain_every=args.maintain_every,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    jarvis = Jarvis(home=args.home)
    try:
        return int(args.func(args, jarvis) or 0)
    except KeyError as exc:
        print(f"오류: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
