"""MCP stdio server, implemented without an SDK dependency.

Tool definitions live in ``mcp_core`` so the HTTP transport exposes exactly the
same surface.

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
from typing import Any

from . import __version__
from .mcp_core import Handler, tools
from .service import Jarvis

PROTOCOL_VERSION = "2024-11-05"


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
                _result(rid, {"tools": tools()})
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
