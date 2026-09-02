"""Per-project memory profiles.

This is the piece OpenViking does not have and the reason MyViking exists: a
coding project and a research project should not accumulate the same *kinds* of
memory. A profile declares, per project:

* which memory categories exist,
* what the distiller should look for in each,
* how many entries to keep and how aggressively to forget,
* how the retrieval token budget is divided between them.

Change the profile and the project's learning behaviour changes — no code edits.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

PROFILE_NAME = "profile.yaml"


@dataclass
class MemoryCategory:
    name: str
    description: str = ""
    # Instruction handed to the distiller for this category.
    extract: str = ""
    # Retrieval priority; higher categories win ties inside the budget.
    priority: int = 5
    # Hard cap on stored entries; the weakest are archived past this.
    keep: int = 30
    # Share of the memory token budget this category may claim (0 = no floor).
    budget_share: float = 0.0
    # If true, entries accumulate into one carrier file per topic (append/merge)
    # rather than one file per observation.
    cumulative: bool = True
    # If true, matches from this category are surfaced as explicit warnings
    # rather than as ordinary context. A pitfall buried as item 7 of 10 gets
    # read as trivia; the point of recording it was to change what happens next.
    warn: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryProfile:
    template: str = "default"
    description: str = ""
    categories: list[MemoryCategory] = field(default_factory=list)
    # Extra guidance appended to every distillation prompt for this project.
    distill_notes: str = ""
    # Optional per-project budget overrides (same keys as BudgetConfig).
    budget: dict[str, int] = field(default_factory=dict)
    # Glob-ish hints about what this project is, used to seed the abstract.
    stack: list[str] = field(default_factory=list)
    # Category that receives observations no rule classified. Empty = derive it.
    fallback: str = ""

    def category(self, name: str) -> MemoryCategory | None:
        for c in self.categories:
            if c.name == name:
                return c
        return None

    def warn_categories(self) -> set[str]:
        return {c.name for c in self.categories if c.warn}

    def category_names(self) -> list[str]:
        return [c.name for c in self.categories]

    def fallback_category(self) -> str:
        """Where an observation goes when no specific category claims it.

        Without this, a project whose profile lacks a catch-all silently drops
        every session that does not match a named rule — the learning loop
        looks like it is running while nothing accumulates.
        """
        if self.fallback:
            return self.fallback
        if not self.categories:
            return ""
        # The lowest-priority category is by definition the least specific.
        return min(self.categories, key=lambda c: (c.priority, c.name)).name

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "description": self.description,
            "distill_notes": self.distill_notes,
            "stack": list(self.stack),
            "fallback": self.fallback,
            "budget": dict(self.budget),
            "categories": [c.to_dict() for c in self.categories],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryProfile":
        data = dict(data or {})
        cats = []
        known = set(MemoryCategory.__dataclass_fields__)
        for raw in data.get("categories") or []:
            if isinstance(raw, str):
                cats.append(MemoryCategory(name=raw))
            else:
                cats.append(
                    MemoryCategory(**{k: v for k, v in raw.items() if k in known})
                )
        return cls(
            template=str(data.get("template", "default")),
            description=str(data.get("description", "")),
            distill_notes=str(data.get("distill_notes", "")),
            stack=list(data.get("stack") or []),
            fallback=str(data.get("fallback", "")),
            budget=dict(data.get("budget") or {}),
            categories=cats or builtin("default").categories,
        )

    def save(self, project_dir: Path) -> Path:
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / PROFILE_NAME
        path.write_text(
            yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, project_dir: Path, template: str = "default") -> "MemoryProfile":
        path = Path(project_dir) / PROFILE_NAME
        if path.exists():
            return cls.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        return builtin(template)


# --------------------------------------------------------------------------
# Built-in templates
# --------------------------------------------------------------------------
def _c(name: str, desc: str, extract: str, **kw: Any) -> MemoryCategory:
    return MemoryCategory(name=name, description=desc, extract=extract, **kw)


_TEMPLATES: dict[str, MemoryProfile] = {
    "default": MemoryProfile(
        template="default",
        description="범용 프로젝트 기본 메모리 스키마",
        categories=[
            _c(
                "preferences",
                "사용자가 반복적으로 요구하는 방식·형식·톤",
                "사용자가 명시적으로 요청하거나 교정한 작업 방식. 일회성 지시는 제외.",
                priority=9,
                keep=25,
                budget_share=0.25,
            ),
            _c(
                "facts",
                "이 프로젝트에 대해 변하지 않는 사실",
                "프로젝트의 목적·제약·환경·핵심 용어처럼 매 세션 다시 설명해야 하는 사실.",
                priority=8,
                keep=40,
                budget_share=0.3,
            ),
            _c(
                "patterns",
                "재사용 가능한 해결 패턴",
                "여러 번 통했던 접근법과 그 적용 조건. 한 번의 성공은 cases에 둘 것.",
                priority=7,
                keep=30,
                budget_share=0.25,
            ),
            _c(
                "cases",
                "구체적 사례와 그 결과",
                "실제 질문·시도·결과 한 건. 실패도 원인과 함께 기록.",
                priority=4,
                keep=60,
                budget_share=0.2,
                cumulative=False,
            ),
        ],
    ),
    "coding": MemoryProfile(
        template="coding",
        description="코드베이스 작업용 스키마",
        distill_notes="파일 경로·명령어·오류 메시지는 원문 그대로 보존할 것.",
        fallback="cases",
        categories=[
            _c(
                "conventions",
                "코드 스타일·네이밍·구조 규칙",
                "이 저장소에서 지켜야 하는 규칙. 리뷰에서 지적된 것 포함.",
                priority=9,
                keep=30,
                budget_share=0.2,
            ),
            _c(
                "architecture",
                "모듈 경계와 데이터 흐름",
                "어떤 모듈이 무엇을 담당하고 어디서 연결되는지. 파일 경로 포함.",
                priority=8,
                keep=25,
                budget_share=0.25,
            ),
            _c(
                "commands",
                "빌드·테스트·배포 명령",
                "실제로 동작이 확인된 명령과 전제 조건. 추측한 명령은 저장 금지.",
                priority=9,
                keep=20,
                budget_share=0.15,
            ),
            _c(
                "pitfalls",
                "반복해서 밟은 함정",
                "실패한 접근과 그 원인, 재발 방지법. 오류 메시지 원문 포함.",
                priority=7,
                keep=40,
                budget_share=0.25,
                warn=True,
            ),
            _c(
                "decisions",
                "기술 선택과 그 이유",
                "선택지·결정·근거·되돌릴 조건.",
                priority=6,
                keep=30,
                budget_share=0.15,
            ),
            _c(
                "cases",
                "구체적 작업 사례",
                "실제 질문·시도·결과 한 건. 위 카테고리에 속하지 않는 것.",
                priority=3,
                keep=60,
                budget_share=0.0,
                cumulative=False,
            ),
        ],
    ),
    "research": MemoryProfile(
        template="research",
        description="조사·분석 프로젝트용 스키마",
        distill_notes="출처가 없는 주장은 저장하지 말고, 있으면 URL을 함께 남길 것.",
        categories=[
            _c(
                "questions",
                "추적 중인 열린 질문",
                "아직 답하지 못한 질문과 현재까지의 가설.",
                priority=8,
                keep=30,
                budget_share=0.2,
            ),
            _c(
                "findings",
                "출처가 있는 확인된 사실",
                "근거와 출처가 확인된 결론만. 각 항목에 출처 표기.",
                priority=9,
                keep=60,
                budget_share=0.4,
            ),
            _c(
                "sources",
                "핵심 참고 자료",
                "반복 참조하는 문서·논문·리포지토리와 그 요지.",
                priority=6,
                keep=40,
                budget_share=0.2,
            ),
            _c(
                "contradictions",
                "상충하는 근거",
                "서로 어긋나는 자료와 무엇이 걸려 있는지.",
                priority=7,
                keep=20,
                budget_share=0.2,
                warn=True,
            ),
        ],
    ),
    "writing": MemoryProfile(
        template="writing",
        description="글쓰기·콘텐츠 프로젝트용 스키마",
        categories=[
            _c(
                "voice",
                "톤·문체 규칙",
                "지켜야 할 문체와 피해야 할 표현. 사용자 교정을 최우선 근거로.",
                priority=10,
                keep=20,
                budget_share=0.3,
            ),
            _c(
                "audience",
                "독자와 그 전제 지식",
                "누가 읽는지, 무엇을 이미 알고 있는지.",
                priority=8,
                keep=15,
                budget_share=0.2,
            ),
            _c(
                "outline",
                "진행 중인 구조와 결정",
                "확정된 목차·논지 순서·삭제된 아이디어와 이유.",
                priority=7,
                keep=25,
                budget_share=0.3,
            ),
            _c(
                "phrasing",
                "재사용할 표현과 금지 표현",
                "사용자가 좋다고 한 문장 패턴, 싫다고 한 표현.",
                priority=6,
                keep=40,
                budget_share=0.2,
                cumulative=False,
            ),
        ],
    ),
    "ops": MemoryProfile(
        template="ops",
        description="인프라·운영용 스키마",
        distill_notes="호스트명·리전·계정 ID는 마스킹하지 말고 그대로, 비밀값은 절대 저장하지 말 것.",
        categories=[
            _c(
                "topology",
                "시스템 구성과 의존성",
                "무엇이 어디서 돌고 무엇에 의존하는지.",
                priority=9,
                keep=30,
                budget_share=0.25,
            ),
            _c(
                "runbooks",
                "검증된 절차",
                "실제로 수행해 성공한 절차. 순서와 확인 지점 포함.",
                priority=9,
                keep=30,
                budget_share=0.3,
            ),
            _c(
                "incidents",
                "장애와 근본 원인",
                "증상·원인·해결·재발 방지.",
                priority=7,
                keep=50,
                budget_share=0.25,
                cumulative=False,
                warn=True,
            ),
            _c(
                "thresholds",
                "정상 범위와 경보 기준",
                "정상 수치와 이상 판단 기준.",
                priority=6,
                keep=25,
                budget_share=0.2,
            ),
        ],
    ),
}


def builtin(template: str) -> MemoryProfile:
    tpl = _TEMPLATES.get(template) or _TEMPLATES["default"]
    # Deep copy so callers can mutate freely.
    return MemoryProfile.from_dict(tpl.to_dict())


def templates() -> list[str]:
    return sorted(_TEMPLATES)


def template_summary() -> list[dict[str, Any]]:
    return [
        {
            "template": name,
            "description": p.description,
            "categories": p.category_names(),
        }
        for name, p in sorted(_TEMPLATES.items())
    ]
