"""MCP stdio server, implemented without an SDK dependency.

MCP over stdio is newline-delimited JSON-RPC 2.0 with a handful of methods, so
implementing it directly keeps MyViking installable with nothing but PyYAML —
and means an SDK version bump cannot break your memory server.

Register it with Claude Code:

    claude mcp add myviking -- /path/to/.venv/bin/python -m jarvis.mcp_server

The tool set is deliberately small. An agent that has to choose between twenty
context tools spends its attention on the choice; these six cover the loop.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

from . import __version__
from .service import Jarvis

PROTOCOL_VERSION = "2024-11-05"


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "jarvis_context",
            "description": (
                "프로젝트의 누적 컨텍스트에서 이 질문에 필요한 부분만 토큰 예산 안에 "
                "골라 반환합니다. 같은 질문을 전에 답한 적이 있으면 그 답을 캐시에서 "
                "바로 돌려줍니다. 프로젝트 작업을 시작할 때 먼저 호출하세요."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "프로젝트 이름"},
                    "question": {"type": "string", "description": "지금 하려는 질문/작업"},
                    "use_cache": {"type": "boolean", "default": True},
                    "max_tier": {
                        "type": "integer",
                        "enum": [0, 1, 2],
                        "default": 2,
                        "description": "0=요약만, 1=개요까지, 2=필요시 전문까지",
                    },
                },
                "required": ["project", "question"],
            },
        },
        {
            "name": "jarvis_remember",
            "description": (
                "이 프로젝트에서 다음에도 쓸 지식을 메모리에 기록합니다. category 는 "
                "해당 프로젝트 프로파일에 정의된 것만 사용할 수 있습니다 "
                "(jarvis_profile 로 확인)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "category": {"type": "string"},
                    "title": {
                        "type": "string",
                        "description": "같은 지식이면 항상 같은 제목을 쓸 것 (파일명이 되어 누적됨)",
                    },
                    "statement": {"type": "string", "description": "한두 문장 요약"},
                    "detail": {"type": "string", "description": "근거·명령어·경로 원문"},
                    "confidence": {"type": "number", "default": 0.8},
                },
                "required": ["project", "category", "title", "statement"],
            },
        },
        {
            "name": "jarvis_commit",
            "description": (
                "질문과 답변을 세션으로 기록하고 메모리로 증류합니다. 작업을 마친 뒤 "
                "호출하면 다음 세션에서 같은 설명을 반복하지 않게 됩니다."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                    "outcome": {"type": "string", "description": "성공/실패 등 결과"},
                    "tokens_in": {"type": "integer", "default": 0},
                    "tokens_out": {"type": "integer", "default": 0},
                },
                "required": ["project", "question", "answer"],
            },
        },
        {
            "name": "jarvis_browse",
            "description": (
                "컨텍스트 저장소를 파일시스템처럼 탐색합니다. op: ls | tree | find | "
                "grep | read. 검색 결과가 왜 그렇게 나왔는지 추적할 때 사용하세요."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["ls", "tree", "find", "grep", "read"]},
                    "uri": {"type": "string", "description": "ls/tree/read/grep 대상 URI"},
                    "project": {"type": "string", "description": "find 에 필요"},
                    "query": {"type": "string", "description": "find/grep 검색어"},
                    "tier": {"type": "integer", "enum": [0, 1, 2], "default": 2},
                    "depth": {"type": "integer", "default": 3},
                    "limit": {"type": "integer", "default": 15},
                },
                "required": ["op"],
            },
        },
        {
            "name": "jarvis_prompt",
            "description": (
                "저장된 프롬프트를 다룹니다. op: list | show | render | save. "
                "반복하는 지시문은 저장해 두고 render 로 변수만 채워 쓰세요."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["list", "show", "render", "save"]},
                    "project": {"type": "string"},
                    "name": {"type": "string"},
                    "template": {"type": "string", "description": "save 용 본문"},
                    "title": {"type": "string"},
                    "values": {"type": "object", "description": "render 용 변수"},
                },
                "required": ["op", "project"],
            },
        },
        {
            "name": "jarvis_profile",
            "description": (
                "프로젝트의 메모리 스키마(카테고리)와 토큰 절감 현황을 보여줍니다. "
                "op: profile | projects | report | templates | init."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["profile", "projects", "report", "templates", "init"],
                        "default": "profile",
                    },
                    "project": {"type": "string"},
                    "template": {"type": "string", "description": "init 용 템플릿"},
                    "description": {"type": "string"},
                },
                "required": ["op"],
            },
        },
    ]


class Handler:
    def __init__(self, jarvis: Jarvis):
        self.j = jarvis

    # ----- tool implementations ---------------------------------------
    def jarvis_context(self, args: dict[str, Any]) -> Any:
        prepared = self.j.prepare(
            args["project"],
            args["question"],
            use_cache=bool(args.get("use_cache", True)),
            max_tier=int(args.get("max_tier", 2)),
        )
        if prepared.cache_hit:
            h = prepared.cache_hit
            return {
                "cache_hit": True,
                "kind": h.kind,
                "similarity": round(h.similarity, 4),
                "original_question": h.question,
                "answer": h.answer,
                "recorded_at": h.created,
                "tokens_saved": h.tokens_saved,
                "note": "이전 답변을 재사용했습니다. 여전히 유효한지 확인하고 사용하세요.",
            }
        pk = prepared.packed
        return {
            "cache_hit": False,
            "context": prepared.context,
            "items": [
                {"uri": i.uri, "tier": f"L{i.tier}", "tokens": i.tokens, "title": i.title}
                for i in (pk.items if pk else [])
            ],
            "tokens_used": pk.tokens if pk else 0,
            "tokens_if_full": pk.baseline_tokens if pk else 0,
            "tokens_if_dump_all": pk.dump_tokens if pk else 0,
            "references": prepared.references,
        }

    def jarvis_remember(self, args: dict[str, Any]) -> Any:
        uri = self.j.remember(
            args["project"],
            args["category"],
            args["title"],
            args["statement"],
            detail=args.get("detail", ""),
            confidence=float(args.get("confidence", 0.8)),
        )
        return {"uri": str(uri)}

    def jarvis_commit(self, args: dict[str, Any]) -> Any:
        return self.j.commit(
            args["project"],
            args["question"],
            args["answer"],
            outcome=args.get("outcome", ""),
            tokens_in=int(args.get("tokens_in", 0)),
            tokens_out=int(args.get("tokens_out", 0)),
        )

    def jarvis_browse(self, args: dict[str, Any]) -> Any:
        op = args["op"]
        if op == "ls":
            return self.j.ls(args["uri"])
        if op == "tree":
            return self.j.tree(args["uri"], depth=int(args.get("depth", 3)))
        if op == "find":
            return self.j.find(
                args.get("query", ""), args["project"], limit=int(args.get("limit", 15))
            )
        if op == "grep":
            return self.j.grep(
                args.get("query", ""), args.get("uri"), limit=int(args.get("limit", 30))
            )
        if op == "read":
            data = self.j.read(args["uri"], tier=int(args.get("tier", 2)))
            if data is None:
                raise ValueError(f"없는 URI: {args['uri']}")
            return data
        raise ValueError(f"알 수 없는 op: {op}")

    def jarvis_prompt(self, args: dict[str, Any]) -> Any:
        op, project = args["op"], args["project"]
        if op == "list":
            return self.j.list_prompts(project)
        if op == "show":
            node = self.j.get_prompt(project, args["name"])
            if node is None:
                raise ValueError("프롬프트가 없습니다")
            return {"uri": str(node.uri), "body": node.body, "vars": node.extra.get("vars", [])}
        if op == "render":
            res = self.j.render_prompt(project, args["name"], args.get("values") or {})
            return {"text": res.text, "tokens": res.tokens, "missing": res.missing}
        if op == "save":
            node = self.j.save_prompt(
                project,
                args["name"],
                args.get("template", ""),
                title=args.get("title", ""),
            )
            return {"uri": str(node.uri), "version": node.extra.get("version")}
        raise ValueError(f"알 수 없는 op: {op}")

    def jarvis_profile(self, args: dict[str, Any]) -> Any:
        op = args.get("op", "profile")
        if op == "projects":
            return self.j.projects()
        if op == "templates":
            return self.j.templates()
        if op == "report":
            return self.j.report(args.get("project")).to_dict()
        if op == "init":
            profile = self.j.init_project(
                args["project"],
                template=args.get("template", "default"),
                description=args.get("description", ""),
            )
            return {"project": args["project"], "profile": profile.to_dict()}
        profile = self.j.profile(args["project"])
        return {
            "project": args["project"],
            "template": profile.template,
            "fallback": profile.fallback_category(),
            "categories": [
                {
                    "name": c.name,
                    "description": c.description,
                    "extract": c.extract,
                    "cumulative": c.cumulative,
                }
                for c in profile.categories
            ],
        }

    def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        fn: Callable[[dict[str, Any]], Any] | None = getattr(self, name, None)
        if fn is None or not name.startswith("jarvis_"):
            raise ValueError(f"알 수 없는 도구: {name}")
        return fn(args)


def _write(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(rid: Any, result: Any) -> None:
    _write({"jsonrpc": "2.0", "id": rid, "result": result})


def _error(rid: Any, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def serve(home: str | None = None) -> None:
    handler = Handler(Jarvis(home=home))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        rid = msg.get("id")
        # Notifications carry no id and expect no response.
        if rid is None and method != "initialize":
            continue

        try:
            if method == "initialize":
                _result(
                    rid,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "myviking", "version": __version__},
                    },
                )
            elif method == "ping":
                _result(rid, {})
            elif method == "tools/list":
                _result(rid, {"tools": _tools()})
            elif method == "tools/call":
                params = msg.get("params") or {}
                name = params.get("name", "")
                args = params.get("arguments") or {}
                try:
                    payload = handler.dispatch(name, args)
                    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
                    _result(rid, {"content": [{"type": "text", "text": text}], "isError": False})
                except Exception as exc:
                    _result(
                        rid,
                        {
                            "content": [{"type": "text", "text": f"오류: {exc}"}],
                            "isError": True,
                        },
                    )
            else:
                _error(rid, -32601, f"지원하지 않는 메서드: {method}")
        except Exception:  # pragma: no cover - keep the server alive
            traceback.print_exc(file=sys.stderr)
            if rid is not None:
                _error(rid, -32603, "내부 오류")


def run() -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="jarvis-mcp", description="MyViking MCP stdio 서버")
    ap.add_argument("--home", help="컨텍스트 저장 위치")
    args = ap.parse_args()
    serve(args.home)


if __name__ == "__main__":
    run()
