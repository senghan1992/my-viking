"""Token accounting.

Every claim about saving tokens is checkable here. Three sources of saving are
tracked separately, because they are earned differently:

* ``cache`` — a repeated question answered from the log; the whole request is
  avoided, so the saving is the full prior cost.
* ``tiering`` — a packed context that used abstracts and overviews where a naive
  approach would have pasted full documents; the saving is the difference.
* ``prompts`` — reusing a stored prompt rather than retyping its context.

The report never invents a baseline: it compares against what the same
retrieval would have cost at full detail, which is measured, not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .db import Database


@dataclass
class UsageReport:
    project: str
    events: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    saved_cache: int = 0
    saved_tiering: int = 0
    baseline: int = 0
    cache_hits: int = 0
    packs: int = 0
    distills: int = 0
    by_event: dict[str, int] = field(default_factory=dict)

    @property
    def saved_total(self) -> int:
        return self.saved_cache + self.saved_tiering

    @property
    def saved_ratio(self) -> float:
        denom = self.baseline
        return (self.saved_total / denom) if denom > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "events": self.events,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "saved_cache": self.saved_cache,
            "saved_tiering": self.saved_tiering,
            "saved_total": self.saved_total,
            "baseline": self.baseline,
            "saved_ratio": round(self.saved_ratio, 4),
            "cache_hits": self.cache_hits,
            "packs": self.packs,
            "distills": self.distills,
            "by_event": self.by_event,
        }


def usage_report(db: Database, project: str | None = None, days: int = 0) -> UsageReport:
    where = []
    params: list[Any] = []
    if project:
        where.append("scope = ?")
        params.append(project)
    if days > 0:
        where.append("ts >= datetime('now', ?)")
        params.append(f"-{int(days)} days")
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    rows = db.query(
        f"SELECT event, COUNT(*) c, SUM(tokens_in) ti, SUM(tokens_out) to_,"
        f" SUM(tokens_saved) ts, SUM(baseline) bl FROM usage{clause} GROUP BY event",
        params,
    )
    rep = UsageReport(project=project or "(all)")
    for r in rows:
        event = r["event"]
        rep.events += r["c"]
        rep.by_event[event] = r["c"]
        rep.tokens_in += r["ti"] or 0
        rep.tokens_out += r["to_"] or 0
        rep.baseline += r["bl"] or 0
        if event == "cache_hit":
            rep.cache_hits = r["c"]
            rep.saved_cache += r["ts"] or 0
        elif event == "pack":
            rep.packs = r["c"]
            rep.saved_tiering += r["ts"] or 0
        elif event == "distill":
            rep.distills = r["c"]
        else:
            rep.saved_tiering += r["ts"] or 0
    return rep


def format_report(rep: UsageReport) -> str:
    lines = [
        f"프로젝트: {rep.project}",
        f"이벤트 {rep.events}건 · 캐시 적중 {rep.cache_hits} · 컨텍스트 패킹 {rep.packs} · 증류 {rep.distills}",
        f"실제 입력 토큰: {rep.tokens_in:,} · 출력 토큰: {rep.tokens_out:,}",
        f"절감(캐시): {rep.saved_cache:,} 토큰",
        f"절감(티어링): {rep.saved_tiering:,} 토큰",
        f"절감 합계: {rep.saved_total:,} / 기준선 {rep.baseline:,} 토큰"
        + (f" ({rep.saved_ratio * 100:.1f}%)" if rep.baseline else ""),
    ]
    return "\n".join(lines)
