"""Per-project prompt library.

Prompts are stored as first-class nodes so they are searchable, tiered and
budgeted like everything else. Two details make them worth a module:

* **Versioning.** Overwriting a prompt keeps the previous text under
  ``prompts/_versions/{name}/v{n}``, so a regression is recoverable.
* **Usage stats.** Each render records how often a prompt is used and what it
  cost. Over time this tells you which prompts to trim — the cheapest token
  saving available is deleting a prompt nobody uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import KIND_PROMPT, Node, Uri, now_iso, slugify
from .store import Store
from .tokens import estimate_tokens

VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
VERSIONS_DIR = "_versions"


@dataclass
class RenderResult:
    text: str
    tokens: int
    missing: list[str]
    uri: str


class PromptLibrary:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------
    def uri(self, project: str, name: str) -> Uri:
        return Uri(project, ("prompts", slugify(name, "prompt")))

    def declared_vars(self, template: str) -> list[str]:
        seen: list[str] = []
        for m in VAR_RE.finditer(template or ""):
            if m.group(1) not in seen:
                seen.append(m.group(1))
        return seen

    # ------------------------------------------------------------------
    def save(
        self,
        project: str,
        name: str,
        template: str,
        title: str = "",
        description: str = "",
        tags: list[str] | None = None,
        keep_version: bool = True,
    ) -> Node:
        uri = self.uri(project, name)
        existing = self.store.read_node(uri)
        version = 1
        if existing is not None:
            version = int(existing.extra.get("version", 1)) + 1
            if keep_version and existing.body.strip() != (template or "").strip():
                self._snapshot(project, name, existing, version - 1)

        node = Node(
            uri=uri,
            kind=KIND_PROMPT,
            title=title or (existing.title if existing else name),
            category="prompt",
            body=template,
            abstract=description or (existing.abstract if existing else ""),
            tags=tags if tags is not None else (existing.tags if existing else []),
            confidence=existing.confidence if existing else 0.6,
            hits=existing.hits if existing else 0,
            created=existing.created if existing else now_iso(),
            extra={
                **(existing.extra if existing else {}),
                "version": version,
                "vars": self.declared_vars(template),
                "tokens": estimate_tokens(template),
            },
        )
        if not node.abstract:
            node.abstract = f"{node.title} 프롬프트 ({len(node.extra['vars'])}개 변수)"
        # A prompt's overview is its own text when short; no point summarising.
        node.overview = node.body if estimate_tokens(node.body) <= 2000 else ""
        return self.store.write_node(node)

    def _snapshot(self, project: str, name: str, old: Node, version: int) -> None:
        snap = Node(
            uri=Uri(project, ("prompts", VERSIONS_DIR, slugify(name), f"v{version}")),
            kind=KIND_PROMPT,
            title=f"{old.title} v{version}",
            category="prompt-version",
            abstract=f"{old.title} 이전 버전 v{version}",
            overview="",
            body=old.body,
            tags=["_version"],
            confidence=0.1,
            created=old.created,
            extra={"version": version, "superseded_at": now_iso()},
        )
        self.store.write_node(snap, regenerate_tiers=False, reinforce_dirs=False)

    # ------------------------------------------------------------------
    def get(self, project: str, name: str) -> Node | None:
        return self.store.read_node(self.uri(project, name))

    def list(self, project: str, include_global: bool = True) -> list[dict[str, Any]]:
        scopes = [project]
        if include_global and project != "global":
            scopes.append("global")
        out: list[dict[str, Any]] = []
        for scope in scopes:
            rows = self.store.db.query(
                "SELECT uri, title, abstract, hits, confidence, updated, tokens_l2"
                " FROM nodes WHERE scope=? AND kind=? ORDER BY hits DESC, updated DESC",
                (scope, KIND_PROMPT),
            )
            for r in rows:
                u = Uri.parse(r["uri"])
                if VERSIONS_DIR in u.parts:
                    continue
                node = self.store.read_node(u)
                out.append(
                    {
                        "uri": r["uri"],
                        "scope": scope,
                        "name": u.name,
                        "title": r["title"],
                        "description": r["abstract"],
                        "uses": r["hits"],
                        "tokens": r["tokens_l2"],
                        "version": int((node.extra if node else {}).get("version", 1)),
                        "vars": list((node.extra if node else {}).get("vars", [])),
                        "updated": r["updated"],
                    }
                )
        return out

    def versions(self, project: str, name: str) -> list[dict[str, Any]]:
        base = Uri(project, ("prompts", VERSIONS_DIR, slugify(name)))
        rows = self.store.db.query(
            "SELECT uri, title, updated, tokens_l2 FROM nodes WHERE scope=? AND kind=?",
            (project, KIND_PROMPT),
        )
        out = []
        for r in rows:
            u = Uri.parse(r["uri"])
            if u.is_under(base) and u != base:
                out.append(
                    {
                        "uri": r["uri"],
                        "version": u.name,
                        "updated": r["updated"],
                        "tokens": r["tokens_l2"],
                    }
                )
        return sorted(out, key=lambda d: d["version"])

    def rollback(self, project: str, name: str, version: str) -> Node | None:
        v = version if str(version).startswith("v") else f"v{version}"
        snap = self.store.read_node(
            Uri(project, ("prompts", VERSIONS_DIR, slugify(name), v))
        )
        if snap is None:
            return None
        return self.save(project, name, snap.body, keep_version=True)

    def delete(self, project: str, name: str) -> bool:
        return self.store.delete_node(self.uri(project, name))

    # ------------------------------------------------------------------
    def render(
        self,
        project: str,
        name: str,
        values: dict[str, Any] | None = None,
        strict: bool = False,
        record: bool = True,
    ) -> RenderResult:
        node = self.get(project, name)
        if node is None and project != "global":
            node = self.store.read_node(Uri("global", ("prompts", slugify(name))))
        if node is None:
            raise KeyError(f"프롬프트를 찾을 수 없습니다: {project}/{name}")

        values = values or {}
        missing = [v for v in self.declared_vars(node.body) if v not in values]
        if strict and missing:
            raise ValueError("누락된 변수: " + ", ".join(missing))

        def sub(m: re.Match[str]) -> str:
            key = m.group(1)
            return str(values[key]) if key in values else m.group(0)

        text = VAR_RE.sub(sub, node.body)
        tokens = estimate_tokens(text)
        if record:
            self.store.touch_node(node.uri, reinforce=0.02)
            self.store.db.log_usage(
                now_iso(),
                project,
                "prompt_render",
                uri=str(node.uri),
                tokens_in=tokens,
                detail={"name": name, "missing": missing},
            )
        return RenderResult(text=text, tokens=tokens, missing=missing, uri=str(node.uri))
