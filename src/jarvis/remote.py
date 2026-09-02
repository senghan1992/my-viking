"""Shell bridge — the adapter for agents that speak neither MCP nor hooks.

Claude Code has hooks, Cursor and Codex have MCP; every other coding agent —
and there is a long tail of them — has exactly one universal capability: it
can run a shell command. So the loop is exposed as commands:

    jv remote brief                    # 세션 시작: 프로젝트 파악
    jv remote ctx "지금 하려는 작업"      # 작업 전: 축적된 컨텍스트
    jv remote remember commands "빌드" "make build 로 빌드한다"
    jv remote commit "질문" "답변" --trace tr_...
    jv remote score tr_... 1.0

Everything talks HTTP to the MyViking server (``MYVIKING_URL`` /
``MYVIKING_KEY``), resolves the project from the checkout's git remote, and
needs only the core install on the agent machine. Output is formatted for an
agent to read, not for a dashboard: context first, ids it must carry clearly
marked.
"""

from __future__ import annotations

import socket
from typing import Any

from .hooks import DEFAULT_URL, HttpTransport, _git_remote


def default_agent() -> str:
    try:
        host = socket.gethostname().split(".")[0]
    except Exception:
        host = "unknown"
    return f"shell@{host}"


def client_for(url: str, key: str = "", timeout: float = 30.0) -> "RemoteClient":
    return RemoteClient(HttpTransport(url or DEFAULT_URL, key, timeout=timeout))


class RemoteClient:
    def __init__(self, transport: Any):
        self.t = transport

    # ----- project resolution -----------------------------------------
    def resolve(
        self, project: str = "", repo: str = "", path: str = "", create: bool = False
    ) -> dict[str, Any]:
        if not repo and path:
            repo = _git_remote(path)
        return self.t.request(
            "POST",
            "/resolve",
            {"project": project, "repo": repo, "path": path, "create": create},
        )

    def resolve_or_fail(
        self, project: str = "", repo: str = "", path: str = "", create: bool = False
    ) -> str:
        res = self.resolve(project, repo, path, create)
        found = str(res.get("project") or "")
        if not found:
            known = ", ".join(res.get("candidates") or []) or "(없음)"
            raise RuntimeError(
                "프로젝트를 특정할 수 없습니다. -p <이름> 또는 --repo <git remote> 를"
                f" 지정하세요. 등록된 프로젝트: {known}"
            )
        return found

    # ----- the loop ------------------------------------------------------
    def brief(self, project: str, limit: int = 8) -> dict[str, Any]:
        return self.t.request(
            "GET", f"/projects/{project}/brief", params={"limit": limit}
        )

    def context(
        self,
        question: str,
        project: str = "",
        repo: str = "",
        path: str = "",
        agent: str = "",
        session_id: str = "",
        max_tier: int = 2,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        if not repo and path:
            repo = _git_remote(path)
        return self.t.request(
            "POST",
            "/prepare",
            {
                "project": project,
                "repo": repo,
                "path": path,
                "question": question,
                "agent": agent or default_agent(),
                "session_id": session_id,
                "max_tier": max_tier,
                "use_cache": use_cache,
            },
        )

    def commit(
        self,
        project: str,
        question: str,
        answer: str,
        trace_id: str = "",
        outcome: str = "",
        model: str = "",
        latency_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        agent: str = "",
    ) -> dict[str, Any]:
        return self.t.request(
            "POST",
            "/commit",
            {
                "project": project,
                "question": question,
                "answer": answer,
                "trace_id": trace_id,
                "outcome": outcome,
                "model": model,
                "latency_ms": latency_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "agent": agent or default_agent(),
            },
        )

    def remember(
        self,
        project: str,
        category: str,
        title: str,
        statement: str,
        detail: str = "",
        confidence: float = 0.8,
    ) -> dict[str, Any]:
        return self.t.request(
            "POST",
            "/memories",
            {
                "project": project,
                "category": category,
                "title": title,
                "statement": statement,
                "detail": detail,
                "confidence": confidence,
            },
        )

    def score(
        self,
        trace_id: str,
        value: float,
        name: str = "helpfulness",
        comment: str = "",
    ) -> dict[str, Any]:
        return self.t.request(
            "POST",
            "/scores",
            {
                "trace_id": trace_id,
                "value": value,
                "name": name,
                "comment": comment,
                "source": "agent",
            },
        )


# --------------------------------------------------------------------------
# formatting — what the agent actually reads
# --------------------------------------------------------------------------
def format_context(prepared: dict[str, Any]) -> str:
    """The /prepare payload as terminal text an agent can act on directly."""
    project = prepared.get("project", "")
    trace_id = prepared.get("trace_id", "")
    lines = [f"[MyViking · {project}] trace_id={trace_id}"]

    hit = prepared.get("cache_hit")
    if hit:
        lines.append(
            f"※ 전에 같은 질문에 답했습니다 (유사도 {hit.get('similarity')},"
            f" {str(hit.get('created') or '')[:10]}). 유효한지 확인 후 재사용하세요:"
        )
        lines.append(str(hit.get("answer") or ""))
        lines.append(f"결과 보고: jv remote score {trace_id} <0..1>")
        return "\n".join(lines)

    warnings = prepared.get("warnings") or []
    if warnings:
        lines.append("⚠ 주의 (거스르는 제안 금지): " + ", ".join(w["title"] for w in warnings))
    context = str(prepared.get("context") or "").strip()
    if context:
        lines.append("아래는 이 프로젝트에서 이미 확인된 내용입니다. 다시 조사하지 마세요.")
        lines.append(context)
    else:
        lines.append("(아직 축적된 컨텍스트가 없습니다. 작업 후 remember/commit 으로 남기세요.)")
    cross = prepared.get("from_other_projects") or []
    if cross:
        lines.append("다른 프로젝트에서 온 참고 (여기서 검증된 것 아님):")
        lines.extend(f"- [{c['project']}] {c['title']}: {c['abstract']}" for c in cross)
    lines.append(
        f"작업이 끝나면: jv remote commit \"<질문>\" \"<답변 요약>\" --trace {trace_id}"
    )
    return "\n".join(lines)
