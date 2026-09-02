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

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLIENTS = ("claude-code", "cursor", "codex", "mcp-json", "shell")

# Claude Code hook events → `jv hook` subcommands. Hooks are what make capture
# unconditional: the client runs them whether or not the agent remembers the
# tools, so every session is recorded even with an empty instruction file.
HOOK_EVENTS = (
    ("SessionStart", "session-start"),
    ("UserPromptSubmit", "user-prompt-submit"),
    ("Stop", "stop"),
    ("SessionEnd", "session-end"),
)


def _in_container() -> bool:
    """Are we generating this config inside the server's container?

    The dashboard and `jv agent config` run server-side; in the Docker
    deployment that is a different machine from where the agent runs."""
    return os.environ.get("MYVIKING_IN_CONTAINER") == "1" or Path("/.dockerenv").exists()


def _jv_command() -> str:
    """The `jv` invocation to bake into hooks. A coding agent's hooks run in a
    non-login shell (often GUI-launched) whose PATH may not include the pipx /
    `pip install --user` bin dir, so a bare `jv` silently fails to start and
    capture quietly never happens. Resolve the absolute path when we can see it
    at config time.

    But when this is generated *inside the server container* (dashboard / `jv
    agent config` on the server), the absolute path is the container's, and the
    agent runs on another machine where it does not exist — baking it there is
    worse than a bare `jv`. So emit `jv` in that case; running `jv agent hooks
    --install` on the agent's own machine rewrites it to that machine's path."""
    import shutil

    if _in_container():
        return "jv"
    return shutil.which("jv") or "jv"


def hook_settings(url: str, key: str = "", timeout: int = 15) -> dict[str, Any]:
    """The ``hooks`` block for Claude Code's ``.claude/settings.json``."""
    jv = _jv_command()

    def command(event: str) -> str:
        env = f"MYVIKING_URL={url.rstrip('/')}"
        if key:
            env += f" MYVIKING_KEY={key}"
        return f"{env} {jv} hook {event}"

    return {
        "hooks": {
            event: [
                {
                    "hooks": [
                        {"type": "command", "command": command(cli), "timeout": timeout}
                    ]
                }
            ]
            for event, cli in HOOK_EVENTS
        }
    }


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
    # Claude Code only: the hooks block that makes capture automatic, and the
    # lighter instruction set that applies once it is installed.
    hooks_setup: str = ""
    hooks_where: str = ""
    instructions_hooks: str = ""

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
            "hooks_setup": self.hooks_setup,
            "hooks_where": self.hooks_where,
            "instructions_hooks": self.instructions_hooks,
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

# The lowest common denominator: an agent that supports neither MCP nor hooks
# can still run shell commands, and that is every coding agent there is. The
# loop is the same; only the verbs change.
INSTRUCTIONS_SHELL = """이 저장소에서 작업할 때는 MyViking 을 셸 명령으로 사용한다.
(MYVIKING_URL / MYVIKING_KEY 환경변수가 설정되어 있어야 한다.)

0. 세션을 시작할 때 먼저 실행한다:
       jv remote brief
   최근 작업·주의사항·미해결이 나온다. 저장소를 처음부터 훑지 말고 여기서 시작한다.
   (연결이 의심되면 `jv remote health` 로 서버·인증 상태를 먼저 확인한다.
    이 저장소를 서버 프로젝트에 고정하려면 `jv remote link -p <이름> --repo <git remote>`.)

1. 개별 작업을 시작하기 전에 실행한다:
       jv remote ctx "지금 하려는 작업 한 줄"
   - 출력된 컨텍스트는 이 프로젝트에서 이미 확인된 사실이다. 다시 조사하지 않는다.
   - ⚠ 주의 항목을 거스르는 제안은 하지 않는다.
   - 첫 줄의 trace_id 를 기억해 둔다.
   - "전에 같은 질문에 답했습니다" 가 나오면 유효한지만 확인하고 재사용한다.

2. 작업 중 새로 확정된 규칙·명령·함정은 즉시 남긴다:
       jv remote remember <카테고리> "<제목>" "<한두 문장>" --detail "<명령·오류 원문>"
   같은 지식은 항상 같은 제목으로 (한 파일에 누적된다).

3. 작업을 마치면 기록한다:
       jv remote commit "<질문>" "<답변 요약>" --trace <trace_id>

4. 사용자가 만족했거나 수정을 요구했으면 평가한다:
       jv remote score <trace_id> <0..1> --comment "<무엇이 좋았/틀렸나>"

모르는 것을 추측해서 기록하지 않는다. 확인된 것만 남긴다."""

# What the agent is asked to do once hooks capture the mechanical half.
# Context arrives injected, every exchange is committed automatically, so the
# instructions shrink to the two things only the agent can judge: what got
# *decided* (remember) and how it *went* (score). Shorter instructions are
# also more likely to be followed.
INSTRUCTIONS_HOOKS = """이 저장소는 MyViking 훅이 컨텍스트 주입과 작업 기록을 자동으로 처리한다.

- 프롬프트에 [MyViking] 블록이 주입되면 그 내용은 이 프로젝트에서 이미 확인된
  사실이다. 같은 것을 다시 조사하지 말고 거기서 시작하고, ⚠ 주의 항목을
  거스르는 제안은 하지 않는다.
- 작업 중 새로 확정된 규칙·명령·결정·함정은 즉시 `jarvis_remember` 로 남긴다.
  기록 자체는 자동이지만, 무엇이 '확정'인지는 에이전트만 안다.
  - 명령·경로·오류 메시지는 원문 그대로. 같은 지식은 항상 같은 title 로.
  - 사용자 개인 선호(프로젝트와 무관)는 project="global".
- 사용자가 결과에 만족하거나 수정을 요구하면 `jarvis_score` 로 보고한다
  (0=틀림, 0.5=보통, 1=도움됨). trace_id 는 주입된 [MyViking] 블록에 있다.
- 더 깊은 컨텍스트가 필요하면 `jarvis_context`/`jarvis_browse`,
  과거 작업 기록은 `jarvis_history`.
- `jarvis_commit` 은 호출하지 않는다 — 훅이 이미 기록한다."""


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

    hooks_setup = hooks_where = instructions_hooks = ""
    if client == "claude-code":
        setup = f"claude mcp add --transport http {name} {mcp_url}{_auth_flag(key)}"
        kind, where = "shell", "붙이려는 머신의 터미널에서 실행하세요."
        hooks_setup = json.dumps(
            hook_settings(base, key), ensure_ascii=False, indent=2
        )
        hooks_where = (
            "저장소의 .claude/settings.json 에 병합하세요. 그 저장소에서 "
            "`jv agent hooks --install` 한 번이면 자동으로 병합됩니다 "
            "(에이전트 머신에 my-viking 코어가 설치되어 있어야 합니다)."
        )
        instructions_hooks = INSTRUCTIONS_HOOKS
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
    elif client == "shell":
        # No MCP endpoint at all: the agent shells out to `jv remote ...`.
        key_line = f"export MYVIKING_KEY={key}\n" if key else ""
        setup = (
            "pip install my-viking          # 코어만 설치됩니다 (의존성: PyYAML 하나)\n"
            f"export MYVIKING_URL={base}\n"
            f"{key_line}jv remote brief                # 연결 확인"
        )
        kind, where = "shell", "에이전트가 도는 머신의 셸 프로파일에 넣으세요."
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
        instructions=(
            INSTRUCTIONS_SHELL if client == "shell" else INSTRUCTIONS.format(project=proj)
        ),
        has_key=bool(key),
        hooks_setup=hooks_setup,
        hooks_where=hooks_where,
        instructions_hooks=instructions_hooks,
    )


def instruction_file(client: str) -> str:
    """Where the instruction block belongs for this client."""
    return {
        "claude-code": "CLAUDE.md",
        "cursor": ".cursorrules",
        "codex": "AGENTS.md",
        "mcp-json": "에이전트 지시문 파일",
        "shell": "AGENTS.md (또는 그 에이전트의 지시문 파일)",
    }.get(client, "에이전트 지시문 파일")
