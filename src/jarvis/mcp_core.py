"""MCP tool surface, shared by the stdio and HTTP transports.

One definition of the tools, two ways to reach them: a local agent speaks stdio,
a remote agent speaks HTTP. Keeping the surface in one place is what makes
"same context from anywhere" true rather than aspirational.

The tool set is deliberately small. An agent choosing between twenty context
tools spends its attention on the choice; these seven cover the loop of
"get context → do work → record what happened → say how it went".
"""

from __future__ import annotations

from typing import Any, Callable

from .models import Uri
from .service import Jarvis

def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "jarvis_context",
            "description": (
                "이 프로젝트에 대해 축적된 컨텍스트에서 지금 필요한 부분만 골라 "
                "반환합니다. 규칙·명령·과거 결정·함정을 이미 알고 시작하므로 탐색 "
                "단계를 건너뛸 수 있습니다. 전에 답한 질문이면 그 답을 바로 돌려줍니다. "
                "프로젝트 작업을 시작할 때 가장 먼저 호출하세요. "
                "project 를 모르면 repo(git remote) 또는 path 를 주면 해석합니다. "
                "반환된 trace_id 는 작업이 끝난 뒤 jarvis_commit / jarvis_score 에 "
                "그대로 넘기세요."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "지금 하려는 질문/작업"},
                    "project": {"type": "string", "description": "프로젝트 이름 (모르면 생략)"},
                    "repo": {
                        "type": "string",
                        "description": "git remote URL. project 없이 이것만으로도 해석됩니다",
                    },
                    "path": {"type": "string", "description": "작업 디렉터리 절대경로"},
                    "agent": {
                        "type": "string",
                        "description": "이 에이전트 식별자 (예: claude-code@laptop)",
                    },
                    "session_id": {"type": "string", "description": "에이전트 세션 ID"},
                    "use_cache": {"type": "boolean", "default": True},
                    "max_tier": {
                        "type": "integer",
                        "enum": [0, 1, 2],
                        "default": 2,
                        "description": "0=요약만, 1=개요까지, 2=필요시 전문까지",
                    },
                },
                "required": ["question"],
            },
        },
        {
            "name": "jarvis_brief",
            "description": (
                "이 프로젝트를 빠르게 파악합니다. 새 세션을 시작할 때, 또는 오랜만에 "
                "돌아온 프로젝트에서 가장 먼저 호출하세요. 확립된 지식, 서 있는 주의사항, "
                "최근에 무슨 작업을 했고 무엇이 새로 정해졌는지, 아직 미해결인 것을 "
                "한 번에 돌려줍니다. 저장소를 처음부터 훑는 대신 여기서 시작하면 됩니다."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "repo": {"type": "string", "description": "git remote URL (project 대신)"},
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "default": 8},
                },
            },
        },
        {
            "name": "jarvis_history",
            "description": (
                "이전 작업 기록을 세션 단위로 되짚습니다. '지난번에 뭘 하다 말았지', "
                "'이 문제 전에 어떻게 처리했지' 를 확인할 때 사용하세요. 각 세션의 "
                "질문·답변·평가가 시간순으로 나옵니다."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "repo": {"type": "string"},
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "default": 8},
                    "session_id": {
                        "type": "string",
                        "description": "특정 세션만 자세히 보려면",
                    },
                },
            },
        },
        {
            "name": "jarvis_remember",
            "description": (
                "다음에도 쓸 지식을 메모리에 기록합니다. category 는 해당 프로젝트 "
                "프로파일에 정의된 것만 사용할 수 있습니다 (jarvis_profile 로 확인). "
                "프로젝트가 아니라 사용자 개인에 관한 것(선호하는 방식·톤·금지사항)은 "
                'project="global" 로 기록하면 모든 프로젝트에 적용됩니다.'
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": '프로젝트 이름, 또는 "global" (모든 프로젝트 공통)',
                    },
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
                    "trace_id": {
                        "type": "string",
                        "description": "jarvis_context 가 준 값. 넘기면 검색과 생성이 한 트레이스로 묶입니다",
                    },
                    "outcome": {"type": "string", "description": "성공/실패 등 결과"},
                    "latency_ms": {"type": "integer", "description": "모델 생성에 걸린 시간"},
                    "tokens_in": {"type": "integer", "default": 0},
                    "tokens_out": {"type": "integer", "default": 0},
                    "agent": {"type": "string"},
                },
                "required": ["project", "question", "answer"],
            },
        },
        {
            "name": "jarvis_score",
            "description": (
                "방금 작업이 어떻게 끝났는지 보고합니다. 이 신호가 그 작업에 사용된 "
                "메모리의 신뢰도를 올리거나 내리므로, 다음 요청의 컨텍스트 품질이 "
                "실제로 개선됩니다. 사용자가 결과에 만족했거나 수정을 요구했을 때 "
                "호출하세요."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "trace_id": {"type": "string", "description": "jarvis_context 가 준 값"},
                    "name": {
                        "type": "string",
                        "default": "helpfulness",
                        "description": "helpfulness | correctness | speed 등",
                    },
                    "value": {
                        "type": "number",
                        "description": "0=완전히 틀렸음, 0.5=중립, 1=정확히 도움됨",
                    },
                    "comment": {"type": "string", "description": "무엇이 좋았거나 틀렸는지"},
                    "uris": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "어떤 지식이 문제였는지 알면 그 uri 만 넘기세요. "
                            "생략하면 검색 기여도에 따라 자동 배분됩니다."
                        ),
                    },
                },
                "required": ["trace_id", "value"],
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
                "프로젝트 상태를 봅니다. op: brief(오랜만에 돌아왔을 때 알아야 할 것 — "
                "확립된 지식·주의사항·미해결), profile(메모리 카테고리), review(확인이 "
                "필요한 항목), digest(전체 프로젝트 요약), projects, templates, init, "
                "metrics(지연·재사용·점수), traces, impact, agents, report(토큰)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": [
                            "profile", "projects", "templates", "init",
                            "brief", "digest", "review",
                            "metrics", "traces", "agents", "impact", "report",
                        ],
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
    def __init__(self, jarvis: Jarvis, key: Any = None):
        self.j = jarvis
        # The API key for this request (or None for stdio/open server). A key
        # scoped to some projects must not read or write the others — MCP is the
        # main surface, so the check lives here rather than only on REST routes.
        self.key = key

    # ----- scope enforcement ------------------------------------------
    def _allowed(self, project: str) -> bool:
        return self.key is None or not project or self.key.allows(project)

    def _check_scope(self, project: str) -> None:
        if not self._allowed(project):
            raise PermissionError(f"이 키는 '{project}' 에 접근할 수 없습니다")

    def _guard_uri(self, uri: str) -> None:
        try:
            scope = Uri.parse(uri).scope
        except Exception:
            return
        self._check_scope(scope)

    def _guard_trace(self, trace_id: str) -> None:
        row = self.j.store.db.one("SELECT scope FROM traces WHERE id=?", (trace_id,))
        if row:
            self._check_scope(row["scope"])

    def _scoped(self) -> bool:
        return self.key is not None and not self.key.allows("*")

    def _deny_cross_project(self, op: str) -> None:
        # Aggregations that span every project leak other scopes' data; a key
        # that isn't all-access must not see them.
        if self._scoped():
            raise PermissionError(
                f"'{op}' 는 전체 프로젝트를 가로지르므로 스코프가 제한된 키로는 볼 수 없습니다"
            )

    # ----- tool implementations ---------------------------------------
    def _project(self, args: dict[str, Any], create: bool = True) -> str:
        """Resolve the project from a name, a git remote, or a path."""
        name = str(args.get("project") or "")
        # A scoped key never invents new projects by repo/path guessing, and may
        # create one only if it is explicitly named and allowed. Otherwise it
        # could reach outside its scope simply by pointing at a fresh checkout.
        self._check_scope(name)
        may_create = create and (not self._scoped() or (bool(name) and self._allowed(name)))
        resolved = self.j.resolve_project(
            project=name,
            repo=str(args.get("repo") or ""),
            path=str(args.get("path") or ""),
            create=may_create,
            # MCP is a coding-agent surface; a project it creates gets the
            # coding categories so pitfalls/decisions have somewhere to land.
            template=str(args.get("template") or "coding"),
        )
        if not resolved["project"]:
            raise ValueError(
                "프로젝트를 특정할 수 없습니다. project 를 지정하거나 repo(git remote)를 "
                f"넘기세요. 등록된 프로젝트: {', '.join(resolved.get('candidates') or []) or '(없음)'}"
            )
        self._check_scope(resolved["project"])
        return resolved["project"]

    def jarvis_context(self, args: dict[str, Any]) -> Any:
        project = self._project(args)
        prepared = self.j.prepare(
            project,
            args["question"],
            use_cache=bool(args.get("use_cache", True)),
            max_tier=int(args.get("max_tier", 2)),
            agent=str(args.get("agent") or ""),
            session_id=str(args.get("session_id") or ""),
        )
        if prepared.cache_hit:
            h = prepared.cache_hit
            return {
                "project": project,
                "trace_id": prepared.trace_id,
                "reused": True,
                "match": h.kind,
                "similarity": round(h.similarity, 4),
                "original_question": h.question,
                "answer": h.answer,
                "recorded_at": h.created,
                "context_ms": prepared.latency_ms,
                "note": (
                    "이 프로젝트에서 전에 답한 내용입니다. 여전히 유효한지 확인한 뒤 "
                    "사용하고, 결과를 jarvis_score 로 알려주세요."
                ),
            }
        pk = prepared.packed
        return {
            "project": project,
            "trace_id": prepared.trace_id,
            "session_id": prepared.session_id,
            # Only on the first call of a sitting.
            "catch_up": prepared.catch_up,
            "reused": False,
            "context": prepared.context,
            "context_ms": prepared.latency_ms,
            "items": [
                {
                    "uri": i.uri,
                    "tier": f"L{i.tier}",
                    "tokens": i.tokens,
                    "title": i.title,
                    "category": i.category,
                }
                for i in (pk.items if pk else [])
            ],
            "warnings": prepared.warnings,
            "from_other_projects": prepared.from_other_projects,
            "budget": {
                "used": pk.tokens if pk else 0,
                "if_full_detail": pk.baseline_tokens if pk else 0,
                "if_dump_everything": pk.dump_tokens if pk else 0,
            },
            "references": prepared.references,
            "note": (
                "위 컨텍스트는 이 프로젝트에 대해 이미 확인된 내용입니다. 다시 조사하지 "
                "말고 여기서 시작하세요. catch_up 이 있으면 이 세션의 첫 호출이라는 "
                "뜻이니, 최근 작업과 새로 정해진 것을 먼저 읽고 시작하세요. "
                "'주의' 항목이 있으면 그것을 거스르는 제안은 "
                "하지 마세요. from_other_projects 는 다른 프로젝트에서 온 참고이며 "
                "이 프로젝트에서 검증된 것이 아니므로, 쓸 때는 출처를 밝히세요. "
                "새로 알게 된 것은 jarvis_remember 로 남기세요."
            ),
        }

    def jarvis_brief(self, args: dict[str, Any]) -> Any:
        project = self._project(args, create=False)
        b = self.j.brief(project, limit=int(args.get("limit", 8)))
        return {
            **b,
            "note": (
                "이 내용은 이 프로젝트에서 이미 확인된 것입니다. 같은 것을 다시 조사하지 "
                "말고 여기서 시작하세요. warnings 를 거스르는 제안은 하지 마세요. "
                "unresolved 는 아직 정해지지 않은 것이니 필요하면 사용자에게 확인하세요."
            ),
        }

    def jarvis_history(self, args: dict[str, Any]) -> Any:
        project = self._project(args, create=False)
        limit = int(args.get("limit", 8))
        sessions = self.j.work_sessions(project, limit=limit)
        wanted = str(args.get("session_id") or "")
        if wanted:
            sessions = [s for s in sessions if s["session_id"] == wanted]
        return {
            "project": project,
            "sessions": sessions,
            "note": (
                "과거 기록입니다. 당시의 내용이며 현재 규칙이 아닙니다 — 이후에 "
                "바뀌었을 수 있으니 현재 규칙은 jarvis_brief 나 jarvis_context 로 "
                "확인하세요."
            ),
        }

    def jarvis_score(self, args: dict[str, Any]) -> Any:
        self._guard_trace(str(args.get("trace_id") or ""))
        return self.j.score(
            args["trace_id"],
            name=str(args.get("name") or "helpfulness"),
            value=float(args["value"]),
            comment=str(args.get("comment") or ""),
            source="agent",
            uris=[str(u) for u in (args.get("uris") or [])] or None,
        )

    def jarvis_remember(self, args: dict[str, Any]) -> Any:
        uri = self.j.remember(
            self._project(args),
            args["category"],
            args["title"],
            args["statement"],
            detail=args.get("detail", ""),
            confidence=float(args.get("confidence", 0.8)),
        )
        return {"uri": str(uri)}

    def jarvis_commit(self, args: dict[str, Any]) -> Any:
        return self.j.commit(
            self._project(args),
            args["question"],
            args["answer"],
            outcome=args.get("outcome", ""),
            tokens_in=int(args.get("tokens_in", 0)),
            tokens_out=int(args.get("tokens_out", 0)),
            trace_id=str(args.get("trace_id") or ""),
            latency_ms=int(args.get("latency_ms", 0)),
            agent=str(args.get("agent") or ""),
        )

    def jarvis_browse(self, args: dict[str, Any]) -> Any:
        op = args["op"]
        if op == "ls":
            self._guard_uri(args["uri"])
            return self.j.ls(args["uri"])
        if op == "tree":
            self._guard_uri(args["uri"])
            return self.j.tree(args["uri"], depth=int(args.get("depth", 3)))
        if op == "find":
            self._check_scope(str(args.get("project") or ""))
            return self.j.find(
                args.get("query", ""), args["project"], limit=int(args.get("limit", 15))
            )
        if op == "grep":
            if args.get("uri"):
                self._guard_uri(str(args["uri"]))
            elif self._scoped():
                # No uri means grep sweeps every project — a scoped key would
                # read outside its scope. Require it to point inside its scope.
                raise PermissionError(
                    "스코프가 제한된 키는 grep 에 자기 프로젝트 uri 를 지정해야 합니다"
                )
            return self.j.grep(
                args.get("query", ""), args.get("uri"), limit=int(args.get("limit", 30))
            )
        if op == "read":
            self._guard_uri(args["uri"])
            data = self.j.read(args["uri"], tier=int(args.get("tier", 2)))
            if data is None:
                raise ValueError(f"없는 URI: {args['uri']}")
            return data
        raise ValueError(f"알 수 없는 op: {op}")

    def jarvis_prompt(self, args: dict[str, Any]) -> Any:
        op, project = args["op"], args["project"]
        self._check_scope(str(project or ""))
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
            rows = self.j.projects()
            if self._scoped():
                rows = [p for p in rows if self._allowed(p["project"])]
            return rows
        if op == "templates":
            return self.j.templates()
        if op == "agents":
            self._deny_cross_project("agents")
            return self.j.agents()
        if op == "brief":
            return self.j.brief(self._project(args, create=False))
        if op == "digest":
            self._deny_cross_project("digest")
            return self.j.digest()
        if op == "review":
            return self.j.review_queue(self._project(args, create=False), limit=30)
        if op == "metrics":
            self._check_scope(str(args.get("project") or ""))
            return self.j.metrics(str(args.get("project") or ""))
        if op == "traces":
            self._check_scope(str(args.get("project") or ""))
            return self.j.traces(str(args.get("project") or ""), limit=20)
        if op == "impact":
            return self.j.memory_impact(self._project(args, create=False))
        if op == "report":
            self._check_scope(str(args.get("project") or ""))
            return self.j.report(args.get("project")).to_dict()
        if op == "init":
            self._check_scope(str(args.get("project") or ""))
            profile = self.j.init_project(
                args["project"],
                template=args.get("template", "default"),
                description=args.get("description", ""),
            )
            return {"project": args["project"], "profile": profile.to_dict()}
        project = self._project(args, create=False)
        profile = self.j.profile(project)
        return {
            "project": project,
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


def dispatch(jarvis: Jarvis, name: str, args: dict[str, Any]) -> Any:
    return Handler(jarvis).dispatch(name, args)


def tool_names() -> list[str]:
    return [t["name"] for t in _tools()]


def tools() -> list[dict[str, Any]]:
    return _tools()
