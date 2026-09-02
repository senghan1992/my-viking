"""The context store: markdown on disk, SQLite for lookup.

Design commitment: **files are the source of truth.** Every memory, prompt and
session is a readable markdown file with YAML frontmatter that you can open,
diff, hand-edit or delete. The index is derived and rebuildable. This is what
makes the system debuggable in a way a vector store is not — you can always
answer "why did it say that?" by reading a file.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Iterable

from .config import Config
from .db import Database
from .embed import Embedder, pack_vector, unpack_vector
from .llm import LLM
from .models import (
    KIND_MEMORY,
    KIND_PROMPT,
    KIND_RESOURCE,
    KIND_SESSION,
    Node,
    Uri,
    body_of,
    now_iso,
    slugify,
)
from .profiles import MemoryProfile
from .tiers import summarize
from .tokens import estimate_tokens

KIND_DIRS = {
    KIND_MEMORY: "memories",
    KIND_PROMPT: "prompts",
    KIND_SESSION: "sessions",
    KIND_RESOURCE: "resources",
}
DIR_KINDS = {v: k for k, v in KIND_DIRS.items()}

GLOBAL_SCOPE = "global"


class Store:
    def __init__(self, config: Config | None = None, db: Database | None = None):
        self.config = config or Config.load()
        self.config.home.mkdir(parents=True, exist_ok=True)
        self.db = db or Database(self.config.db_path)
        self.embedder = Embedder.from_config(self.config)
        self.llm = LLM.from_config(self.config)
        self._profiles: dict[str, MemoryProfile] = {}

    # ------------------------------------------------------------------
    # paths & scopes
    # ------------------------------------------------------------------
    def scope_dir(self, scope: str) -> Path:
        if scope == GLOBAL_SCOPE:
            return self.config.home / "global"
        return self.config.project_dir(scope)

    def path_for(self, uri: Uri) -> Path:
        base = self.scope_dir(uri.scope)
        if not uri.parts:
            return base
        return base.joinpath(*uri.parts).with_suffix(".md")

    def dir_path_for(self, uri: Uri) -> Path:
        base = self.scope_dir(uri.scope)
        return base.joinpath(*uri.parts) if uri.parts else base

    def uri_for_path(self, path: Path) -> Uri | None:
        path = Path(path).resolve()
        home = self.config.home.resolve()
        try:
            rel = path.relative_to(home)
        except ValueError:
            return None
        parts = list(rel.parts)
        if not parts:
            return None
        if parts[0] == "global":
            scope, rest = GLOBAL_SCOPE, parts[1:]
        elif parts[0] == "projects" and len(parts) > 1:
            scope, rest = parts[1], parts[2:]
        else:
            return None
        if rest and rest[-1].endswith(".md"):
            rest[-1] = rest[-1][:-3]
        return Uri(scope, tuple(rest))

    # ------------------------------------------------------------------
    # projects
    # ------------------------------------------------------------------
    def projects(self) -> list[str]:
        out = []
        if self.config.projects_dir.exists():
            out = sorted(
                p.name for p in self.config.projects_dir.iterdir() if p.is_dir()
            )
        return out

    def project_exists(self, project: str) -> bool:
        return self.scope_dir(project).exists()

    def ensure_project(
        self,
        project: str,
        template: str = "default",
        description: str = "",
        stack: Iterable[str] | None = None,
    ) -> MemoryProfile:
        """Create a project's directory tree and memory profile if missing."""
        if project == GLOBAL_SCOPE:
            base = self.scope_dir(GLOBAL_SCOPE)
            for d in KIND_DIRS.values():
                (base / d).mkdir(parents=True, exist_ok=True)
            return self.profile(GLOBAL_SCOPE)

        base = self.scope_dir(project)
        fresh = not (base / "profile.yaml").exists()
        for d in KIND_DIRS.values():
            (base / d).mkdir(parents=True, exist_ok=True)
        profile = MemoryProfile.load(base, template)
        if fresh:
            profile = MemoryProfile.from_dict(
                {**profile.to_dict(), "template": template}
            )
            if description:
                profile.description = description
            if stack:
                profile.stack = list(stack)
            profile.save(base)
        # Memory categories become real directories so `ls`/`tree` are honest.
        for cat in profile.categories:
            (base / "memories" / cat.name).mkdir(parents=True, exist_ok=True)
        self._profiles[project] = profile
        return profile

    def profile(self, project: str) -> MemoryProfile:
        if project not in self._profiles:
            self._profiles[project] = MemoryProfile.load(self.scope_dir(project))
        return self._profiles[project]

    def save_profile(self, project: str, profile: MemoryProfile) -> Path:
        self._profiles[project] = profile
        for cat in profile.categories:
            (self.scope_dir(project) / "memories" / cat.name).mkdir(
                parents=True, exist_ok=True
            )
        return profile.save(self.scope_dir(project))

    def delete_project(self, project: str) -> bool:
        if project == GLOBAL_SCOPE:
            raise ValueError("global 스코프는 삭제할 수 없습니다")
        base = self.scope_dir(project)
        if not base.exists():
            return False
        shutil.rmtree(base)
        self.db.clear_scope(project)
        self.db.execute("DELETE FROM cache WHERE scope = ?", (project,))
        self.db.commit()
        self._profiles.pop(project, None)
        return True

    # ------------------------------------------------------------------
    # node write / read
    # ------------------------------------------------------------------
    def make_uri(
        self, project: str, kind: str, *rest: str, title: str = ""
    ) -> Uri:
        kind_dir = KIND_DIRS.get(kind, kind)
        segs = [s for r in rest for s in str(r).split("/") if s]
        if not segs:
            segs = [slugify(title, "item")]
        return Uri(project, (kind_dir, *segs))

    def write_node(
        self,
        node: Node,
        regenerate_tiers: bool = True,
        reinforce_dirs: bool = True,
    ) -> Node:
        """Persist a node, generating tiers and updating the index."""
        if regenerate_tiers and (not node.abstract or not node.overview):
            abstract, overview = summarize(
                node.body or node.overview, node.title, self.llm
            )
            node.abstract = node.abstract or abstract
            node.overview = node.overview or overview
        if not node.title:
            node.title = node.uri.name.replace("-", " ")

        # Tier invariant: L0 <= L1 <= L2 in cost. A summary that is longer than
        # what it summarises is not a summary, and it breaks the packer's
        # assumption that a deeper tier buys more information per token.
        if node.body.strip() and estimate_tokens(node.overview) > estimate_tokens(node.body):
            node.overview = node.body
        if node.overview.strip() and estimate_tokens(node.abstract) > estimate_tokens(node.overview):
            node.abstract = node.overview

        node.updated = now_iso()

        path = self.path_for(node.uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(node.to_markdown(), encoding="utf-8")
        old_vector, new_vector = self._index_node(node, path)
        self._bump_ancestors(node, old_vector, new_vector, is_new=not old_vector)
        self.db.commit()
        return node

    def read_node(self, uri: Uri | str) -> Node | None:
        u = uri if isinstance(uri, Uri) else Uri.parse(uri)
        path = self.path_for(u)
        if not path.exists():
            return None
        return Node.from_markdown(path.read_text(encoding="utf-8"), u)

    def read_tier(self, uri: Uri | str, tier: int) -> str:
        """Return one tier's text as cheaply as possible.

        L0/L1 live in the index, so they cost a row lookup. Only L2 touches the
        filesystem, and it skips YAML parsing entirely.
        """
        u = uri if isinstance(uri, Uri) else Uri.parse(uri)
        row = self.db.one(
            "SELECT abstract, overview, title, path FROM nodes WHERE uri = ?", (str(u),)
        )
        if row is None:
            node = self.read_node(u)
            return node.tier(tier) if node else ""
        if tier <= 0:
            return row["abstract"] or row["title"] or ""
        if tier == 1:
            return row["overview"] or row["abstract"] or row["title"] or ""
        path = Path(row["path"]) if row["path"] else self.path_for(u)
        if path.exists():
            body = body_of(path.read_text(encoding="utf-8"))
            if body:
                return body
        return row["overview"] or row["abstract"] or row["title"] or ""

    def delete_node(self, uri: Uri | str) -> bool:
        u = uri if isinstance(uri, Uri) else Uri.parse(uri)
        path = self.path_for(u)
        existed = path.exists()
        row = self.db.one("SELECT vector, title FROM nodes WHERE uri = ?", (str(u),))
        if existed:
            path.unlink()
        if row is not None:
            stub = Node(uri=u, title=row["title"] or u.name)
            self._bump_ancestors(
                stub, unpack_vector(row["vector"]), [], is_new=False, removing=True
            )
        self.db.delete_node(str(u))
        self.db.commit()
        return existed

    def archive_node(self, uri: Uri | str, reason: str = "") -> Uri | None:
        """Move a node under ``_archive/`` instead of destroying it.

        Forgetting must be reversible: a memory archived by decay is often the
        one you need when the project comes back to life.
        """
        u = uri if isinstance(uri, Uri) else Uri.parse(uri)
        node = self.read_node(u)
        if node is None:
            return None
        target = Uri(u.scope, ("_archive", *u.parts))
        node.uri = target
        node.extra = {**node.extra, "archived_at": now_iso(), "archive_reason": reason}
        self.delete_node(u)
        self.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
        return target

    def touch_node(self, uri: Uri | str, reinforce: float = 0.0) -> None:
        """Record that a node was actually used, and optionally reinforce it."""
        u = uri if isinstance(uri, Uri) else Uri.parse(uri)
        node = self.read_node(u)
        if node is None:
            return
        node.hits += 1
        node.last_used = now_iso()
        if reinforce:
            node.confidence = min(1.0, node.confidence + reinforce)
        path = self.path_for(u)
        path.write_text(node.to_markdown(), encoding="utf-8")
        self.db.execute(
            "UPDATE nodes SET hits=?, last_used=?, confidence=? WHERE uri=?",
            (node.hits, node.last_used, node.confidence, str(u)),
        )
        self.db.commit()

    # ------------------------------------------------------------------
    # indexing
    # ------------------------------------------------------------------
    def _index_node(self, node: Node, path: Path) -> tuple[list[float], list[float]]:
        """Index one node. Returns ``(old_vector, new_vector)`` so the caller can
        adjust ancestor centroids incrementally."""
        text = node.searchable_text()
        vector = self.embedder.embed(
            "\n".join([node.title, node.abstract, node.overview])[:8000] or text[:8000]
        )
        prev = self.db.one("SELECT vector FROM nodes WHERE uri = ?", (str(node.uri),))
        old_vector = unpack_vector(prev["vector"]) if prev else []
        parent = node.uri.parent
        self.db.upsert_node(
            {
                "uri": str(node.uri),
                "scope": node.uri.scope,
                "kind": node.kind,
                "category": node.category,
                "title": node.title,
                "abstract": node.abstract,
                "overview": node.overview,
                "tags": node.tags,
                "confidence": float(node.confidence),
                "hits": int(node.hits),
                "created": node.created,
                "updated": node.updated,
                "last_used": node.last_used,
                "tokens_l0": estimate_tokens(node.tier(0)),
                "tokens_l1": estimate_tokens(node.tier(1)),
                "tokens_l2": estimate_tokens(node.tier(2)),
                "path": str(path),
                "parent": str(parent) if parent else "",
                "vector": pack_vector(vector),
            }
        )
        self.db.upsert_fts(str(node.uri), text)
        return old_vector, vector

    def _bump_ancestors(
        self,
        node: Node,
        old_vector: list[float],
        new_vector: list[float],
        is_new: bool,
        removing: bool = False,
    ) -> None:
        """Fold one node's vector change into each ancestor directory's sum."""
        label = node.title or node.uri.name
        for anc in node.uri.ancestors():
            key = str(anc)
            row = self.db.dir_row(key)
            total = unpack_vector(row["vector"]) if row else []
            children = int(row["children"]) if row else 0
            dim = len(new_vector) or len(old_vector) or len(total)
            if not total:
                total = [0.0] * dim
            if removing:
                for i, v in enumerate(old_vector):
                    total[i] -= v
                children = max(0, children - 1)
            else:
                for i, v in enumerate(new_vector):
                    total[i] += v
                for i, v in enumerate(old_vector):
                    total[i] -= v
                if is_new:
                    children += 1
            names = self._dir_label(row, label, children, removing)
            self.db.upsert_dir(
                {
                    "uri": key,
                    "scope": anc.scope,
                    "kind": anc.kind_dir,
                    "children": children,
                    "abstract": names,
                    "updated": now_iso(),
                    "vector": pack_vector(total),
                }
            )
        self.db.prune_empty_dirs(node.uri.scope)

    @staticmethod
    def _dir_label(row: Any, label: str, children: int, removing: bool) -> str:
        """Keep a short, human-readable sample of what a directory holds."""
        existing = ""
        if row and row["abstract"] and ": " in row["abstract"]:
            existing = row["abstract"].split(": ", 1)[1]
        names = [n for n in (x.strip() for x in existing.split(",")) if n]
        if removing:
            names = [n for n in names if n != label]
        elif label not in names and len(names) < 8:
            names.append(label)
        return f"{children}개 항목: {', '.join(names)}"

    def reindex(self, project: str | None = None) -> dict[str, int]:
        """Rebuild the index from the markdown files."""
        scopes = [project] if project else [GLOBAL_SCOPE, *self.projects()]
        counts = {"nodes": 0, "scopes": 0}
        for scope in scopes:
            base = self.scope_dir(scope)
            if not base.exists():
                continue
            self.db.clear_scope(scope)
            for path in sorted(base.rglob("*.md")):
                uri = self.uri_for_path(path)
                if uri is None:
                    continue
                try:
                    node = Node.from_markdown(path.read_text(encoding="utf-8"), uri)
                except Exception:
                    continue
                self._index_node(node, path)
                counts["nodes"] += 1
            self.refresh_dirs(scope)
            counts["scopes"] += 1
        self.db.commit()
        return counts

    def refresh_dirs(self, scope: str) -> None:
        """Recompute directory centroids and abstracts for coarse retrieval.

        A directory's vector is the mean of its descendants' vectors. Searching
        directories first, then drilling in, keeps the number of L0 reads
        proportional to depth rather than to corpus size.
        """
        rows = self.db.query(
            "SELECT uri, abstract, title, vector, tokens_l0 FROM nodes WHERE scope=?",
            (scope,),
        )
        agg: dict[str, dict] = {}
        for row in rows:
            uri = Uri.parse(row["uri"])
            vec = unpack_vector(row["vector"])
            for anc in uri.ancestors():
                key = str(anc)
                slot = agg.setdefault(
                    key, {"n": 0, "sum": None, "titles": [], "kind": anc.kind_dir}
                )
                slot["n"] += 1
                if vec:
                    if slot["sum"] is None:
                        slot["sum"] = list(vec)
                    else:
                        for i, v in enumerate(vec):
                            slot["sum"][i] += v
                if len(slot["titles"]) < 8:
                    slot["titles"].append(row["title"] or uri.name)

        self.db.execute("DELETE FROM dirs WHERE scope=?", (scope,))
        for key, slot in agg.items():
            # Stored unnormalised: incremental writes add to this sum.
            vec = slot["sum"] or []
            names = ", ".join(slot["titles"])
            self.db.upsert_dir(
                {
                    "uri": key,
                    "scope": scope,
                    "kind": slot["kind"],
                    "children": slot["n"],
                    "abstract": f"{slot['n']}개 항목: {names}",
                    "updated": now_iso(),
                    "vector": pack_vector(vec) if vec else None,
                }
            )
        self.db.commit()

    # ------------------------------------------------------------------
    # browsing
    # ------------------------------------------------------------------
    def ls(self, uri: Uri | str) -> dict:
        u = uri if isinstance(uri, Uri) else Uri.parse(uri)
        prefix = str(u)
        dirs = self.db.query(
            "SELECT uri, children, abstract FROM dirs WHERE scope=?", (u.scope,)
        )
        depth = len(u.parts)
        child_dirs = []
        for row in dirs:
            d = Uri.parse(row["uri"])
            if d.is_under(u) and len(d.parts) == depth + 1:
                child_dirs.append(
                    {
                        "uri": row["uri"],
                        "name": d.name,
                        "children": row["children"],
                        "abstract": row["abstract"],
                        "type": "dir",
                    }
                )
        nodes = self.db.query(
            "SELECT uri, kind, title, abstract, category, confidence, hits,"
            " tokens_l0, tokens_l2, updated FROM nodes"
            " WHERE scope=? AND parent=? ORDER BY confidence DESC, updated DESC",
            (u.scope, prefix),
        )
        return {
            "uri": prefix,
            "dirs": sorted(child_dirs, key=lambda d: d["name"]),
            "nodes": [
                {
                    "uri": r["uri"],
                    "name": Uri.parse(r["uri"]).name,
                    "kind": r["kind"],
                    "title": r["title"],
                    "abstract": r["abstract"],
                    "category": r["category"],
                    "confidence": round(r["confidence"], 3),
                    "hits": r["hits"],
                    "tokens": {"l0": r["tokens_l0"], "l2": r["tokens_l2"]},
                    "updated": r["updated"],
                    "type": "node",
                }
                for r in nodes
            ],
        }

    def tree(self, uri: Uri | str, depth: int = 3) -> dict:
        u = uri if isinstance(uri, Uri) else Uri.parse(uri)

        def build(cur: Uri, level: int) -> dict:
            listing = self.ls(cur)
            out = {
                "uri": str(cur),
                "name": cur.name,
                "type": "dir",
                "nodes": listing["nodes"],
                "dirs": [],
            }
            if level < depth:
                out["dirs"] = [build(Uri.parse(d["uri"]), level + 1) for d in listing["dirs"]]
            else:
                out["dirs"] = [
                    {"uri": d["uri"], "name": d["name"], "type": "dir", "truncated": True}
                    for d in listing["dirs"]
                ]
            return out

        return build(u, 0)

    def grep(self, term: str, uri: Uri | str | None = None, limit: int = 30) -> list[dict]:
        """Literal substring search over stored bodies."""
        scopes = [Uri.parse(uri).scope] if uri else [GLOBAL_SCOPE, *self.projects()]
        root = Uri.parse(uri) if uri else None
        out: list[dict] = []
        needle = term.lower()
        for scope in scopes:
            for row in self.db.query(
                "SELECT uri, path, title FROM nodes WHERE scope=?", (scope,)
            ):
                node_uri = Uri.parse(row["uri"])
                if root is not None and not node_uri.is_under(root):
                    continue
                path = Path(row["path"])
                if not path.exists():
                    continue
                for i, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    if needle in line.lower():
                        out.append(
                            {
                                "uri": row["uri"],
                                "title": row["title"],
                                "line": i,
                                "text": line.strip()[:300],
                            }
                        )
                        if len(out) >= limit:
                            return out
        return out

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------
    def stats(self, project: str | None = None) -> dict:
        scopes = [project] if project else [GLOBAL_SCOPE, *self.projects()]
        out: dict[str, dict] = {}
        for scope in scopes:
            rows = self.db.query(
                "SELECT kind, COUNT(*) c, SUM(tokens_l0) t0, SUM(tokens_l2) t2"
                " FROM nodes WHERE scope=? GROUP BY kind",
                (scope,),
            )
            by_kind = {
                r["kind"]: {
                    "count": r["c"],
                    "tokens_l0": r["t0"] or 0,
                    "tokens_l2": r["t2"] or 0,
                }
                for r in rows
            }
            cache = self.db.one(
                "SELECT COUNT(*) c, SUM(hits) h FROM cache WHERE scope=?", (scope,)
            )
            out[scope] = {
                "by_kind": by_kind,
                "total_nodes": sum(v["count"] for v in by_kind.values()),
                "tokens_l0": sum(v["tokens_l0"] for v in by_kind.values()),
                "tokens_l2": sum(v["tokens_l2"] for v in by_kind.values()),
                "cache_entries": (cache["c"] if cache else 0) or 0,
                "cache_hits": (cache["h"] if cache else 0) or 0,
                "profile": self.profile(scope).template if scope != GLOBAL_SCOPE else "-",
            }
        return out
