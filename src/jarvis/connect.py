"""Connection info for coding agents.

The only thing a person needs from MyViking is this: what do I paste into my
coding agent so that this project's knowledge starts accumulating? So the
snippets live in one module, shared by the CLI and the dashboard, rather than
being a CLI feature the UI cannot reach.

Two halves matter equally:

* the **MCP registration** — wires the tools up,
* the **agent instructions** — tells the agent when to use them.

Wiring alone does nothing: an agent with tools it was never told to call will
not call them, and the knowledge base stays empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CLIENTS = ("claude-code", "cursor", "codex", "mcp-json")


@dataclass
class Connection:
    client: str
    project: str
    url: str
    mcp_url: str
    setup: str
    setup_kind: str  # "shell" | "json" | "toml"
    where: str
    instructions: str
    has_key: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "client": self.client,
            "project": self.project,
            "url": self.url,
            "mcp_url": self.mcp_url,
            "setup": self.setup,
            "setup_kind": self.setup_kind,
            "where": self.where,
            "instructions": self.instructions,
            "has_key": self.has_key,
        }


# The instruction block is the half that makes the loop self-sustaining: the
# agent reads context, records what it learned, and reports how it went. Nobody
# has to curate anything for the knowledge base to improve.
INSTRUCTIONS = """이 저장소에서 작업할 때는 MyViking 을 프로젝트 지식의 원천으로 사용한다.

0. 세션을 시작할 때 먼저 `jarvis_brief` 를 호출해 프로젝트를 파악한다.
   - project="{project}" (또는 repo 에 git remote URL)
   - 확립된 지식·주의사항·최근 작업·미해결 사항이 한 번에 나온다.
     저장소를 처음부터 훑지 말고 여기서 시작한다.
   - "지난번에 뭘 하다 말았지" 가 필요하면 `jarvis_history` 로 세션 단위 기록을 본다.

1. 개별 작업을 시작하기 전에 `jarvis_context` 를 호출한다.
   - project="{project}" (또는 repo 에 git remote URL)
   - 돌아온 컨텍스트는 이 프로젝트에서 이미 확인된 사실이다. 같은 것을 다시
     조사하지 말고 거기서 시작한다.
   - `warnings` 가 있으면 그것을 거스르는 제안은 하지 않는다.
   - `reused: true` 면 이전에 같은 질문에 답한 내용이다. 여전히 유효한지만
     확인하고 재사용한다.
   - `from_other_projects` 는 다른 프로젝트에서 온 참고이며 이 프로젝트에서
     검증된 것이 아니다. 쓸 때는 출처를 밝힌다.
   - `catch_up` 이 들어오면 이 세션의 첫 호출이다. 최근 작업과 새로 정해진 것을
     먼저 읽는다.
   - 반환된 `trace_id` 를 이후 호출에 그대로 넘긴다.

2. 작업 중 새로 확정된 것은 즉시 `jarvis_remember` 로 남긴다.
   - 명령·경로·오류 메시지는 원문 그대로.
   - 실패한 접근과 그 원인도 남긴다 (다음에 같은 함정을 피하게 된다).
   - 같은 지식은 항상 같은 title 을 쓴다 (한 파일에 누적된다).
   - 프로젝트가 아니라 사용자 개인에 관한 것이면 project="global".

3. 작업을 마치면 `jarvis_commit` 에 trace_id·질문·결과를 기록한다.

4. 결과가 어땠는지 `jarvis_score` 로 알린다 (0=틀림, 0.5=보통, 1=도움됨).
   이 값이 사용된 지식의 신뢰도를 조정하므로, 다음 요청의 품질이 실제로 달라진다.
   어떤 지식이 틀렸는지 알면 `uris` 로 지목한다.

모르는 것을 추측해서 기록하지 않는다. 확인된 것만 남긴다."""


def _auth_flag(key: str) -> str:
    return f' --header "Authorization: Bearer {key}"' if key else ""


def _json_headers(key: str) -> str:
    return f',\n      "headers": {{ "Authorization": "Bearer {key}" }}' if key else ""


def _toml_headers(key: str) -> str:
    return f'\nheaders = {{ Authorization = "Bearer {key}" }}' if key else ""


def build(
    client: str,
    url: str,
    project: str = "",
    key: str = "",
    name: str = "myviking",
) -> Connection:
    """Compose the paste-ready setup for one client."""
    if client not in CLIENTS:
        raise ValueError(
            f"알 수 없는 클라이언트: {client}. 사용 가능: {', '.join(CLIENTS)}"
        )
    base = url.rstrip("/")
    mcp_url = f"{base}/mcp"
    proj = project or "<프로젝트>"

    if client == "claude-code":
        setup = f"claude mcp add --transport http {name} {mcp_url}{_auth_flag(key)}"
        kind, where = "shell", "붙이려는 머신의 터미널에서 실행하세요."
    elif client == "cursor":
        setup = (
            "{\n"
            '  "mcpServers": {\n'
            f'    "{name}": {{\n'
            f'      "url": "{mcp_url}"{_json_headers(key)}\n'
            "    }\n"
            "  }\n"
            "}"
        )
        kind, where = "json", "~/.cursor/mcp.json 에 넣으세요."
    elif client == "codex":
        setup = f"[mcp_servers.{name}]\nurl = \"{mcp_url}\"{_toml_headers(key)}"
        kind, where = "toml", "~/.codex/config.toml 에 넣으세요."
    else:  # mcp-json — 그 외 MCP 클라이언트 공통 형식
        setup = (
            "{\n"
            f'  "{name}": {{\n'
            '    "type": "http",\n'
            f'    "url": "{mcp_url}"{_json_headers(key)}\n'
            "  }\n"
            "}"
        )
        kind, where = "json", "클라이언트의 MCP 서버 설정에 넣으세요."

    return Connection(
        client=client,
        project=project,
        url=base,
        mcp_url=mcp_url,
        setup=setup,
        setup_kind=kind,
        where=where,
        instructions=INSTRUCTIONS.format(project=proj),
        has_key=bool(key),
    )


def instruction_file(client: str) -> str:
    """Where the instruction block belongs for this client."""
    return {
        "claude-code": "CLAUDE.md",
        "cursor": ".cursorrules",
        "codex": "AGENTS.md",
        "mcp-json": "에이전트 지시문 파일",
    }.get(client, "에이전트 지시문 파일")
