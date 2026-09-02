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

    sp = sub.add_parser("serve", help="HTTP API 서버 실행")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8787)
    sp.set_defaults(func=cmd_serve)

    return p


def cmd_serve(args, j: Jarvis) -> int:
    from .server import run

    run(host=args.host, port=args.port, home=args.home)
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
