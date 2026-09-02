"""MyViking — 프로젝트별 자가학습 컨텍스트 데이터베이스.

OpenViking(volcengine)의 세 가지 아이디어를 참고했습니다: 컨텍스트를 가상
파일시스템으로 다루기, L0/L1/L2 티어 로딩, 세션에서 장기 메모리 증류.
여기에 두 가지를 더했습니다: 프로젝트마다 다른 메모리 스키마(프로파일)와,
절감 토큰을 추정이 아니라 측정으로 남기는 토큰 회계.
"""

from .config import BudgetConfig, Config, EmbedConfig, LearnConfig, LLMConfig
from .learn import DistillReport, Learner, MemoryCandidate
from .models import Node, Uri
from .profiles import MemoryCategory, MemoryProfile, builtin, templates
from .retrieve import PackedContext, Retriever
from .service import Jarvis, Prepared
from .sessions import CacheHit, SessionLog
from .store import Store

__version__ = "0.1.0"

__all__ = [
    "Jarvis",
    "Prepared",
    "Config",
    "BudgetConfig",
    "EmbedConfig",
    "LearnConfig",
    "LLMConfig",
    "MemoryProfile",
    "MemoryCategory",
    "builtin",
    "templates",
    "Node",
    "Uri",
    "Store",
    "Retriever",
    "PackedContext",
    "SessionLog",
    "CacheHit",
    "Learner",
    "DistillReport",
    "MemoryCandidate",
    "__version__",
]
