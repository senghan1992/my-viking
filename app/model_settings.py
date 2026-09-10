"""웹 대시보드 '모델 설정' — data/models.json (0600).

환경변수를 몰라도 관리자 화면에서 LLM/임베딩 제공자를 설정할 수 있게 합니다.

우선순위: **웹 설정 > 환경변수(VIKING_LLM_* / VIKING_EMBED_*) > 기본값**
저장 시 즉시 config 에 반영되어 재시작이 필요 없습니다 (핫스왑).

파일 구조:
  llm_base_url / llm_api_key / llm_model / embed_base_url / embed_api_key / embed_model
  registered — 관리자가 직접 등록한 커스텀 모델 목록 (드롭다운 선택용):
    [{id, kind(llm|embed), name, base_url, model, api_key?, endpoint(auto|direct)}]
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

FIELDS = (
    "llm_base_url", "llm_api_key", "llm_model",
    "embed_base_url", "embed_api_key", "embed_model",
)

_id_re = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

def _DEFAULTS() -> dict:
    return {
        "llm_base_url": "https://api.openai.com/v1",
        "llm_model": "gpt-4o-mini",
        "embed_model": "text-embedding-3-small",
    }


_DEF = _DEFAULTS()


# ── 첫 실행 시드 (models_seed.json) ──────────────────────────────── #
def _seed_path() -> Path:
    """배포에 딸려 가는 기본 시드 파일 (app/models_seed.json)."""
    return Path(__file__).resolve().parent / "models_seed.json"


def seed_if_empty() -> bool:
    """관리자가 아무것도 건드리기 전 첫 실행에만 기본 모델 구성을 넣는다.

    - llm 설정: app/models_seed.json 의 llm (배포본 = pi 의 models.json(databricks) 기준)
    - registered: 사전 등록 카탈로그 전체를 드롭다운에 미리 채운다
    이미 웹 설정이나 등록 모델이 있으면 아무것도 하지 않는다 (관리자 결정 존중).
    VIKING_SEED_MODELS=false 로 끌 수 있다.
    """
    if os.environ.get("VIKING_SEED_MODELS", "true").lower() == "false":
        return False
    p = file_path()
    if p.exists():
        data = _load_raw()
        if data.get("registered") or any(data.get(k) for k in FIELDS):
            return False  # 이미 설정됨
    seed_file = _seed_path()
    if not seed_file.exists():
        return False
    try:
        seed = json.loads(seed_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    llm = seed.get("llm") or {}
    # 시드 파일의 api_key 는 절대 쓰지 않는다 (GitHub push 보호에 걸린 사고 —
    # 비밀은 저장소에 넣지 않는다). 키는 VIKING_LLM_API_KEY 또는 관리자 화면에서.
    _map = {"base_url": "llm_base_url", "api_key": "llm_api_key", "model": "llm_model"}
    values = {_map.get(k, k): str(v).strip()
              for k, v in llm.items() if _map.get(k, k) in FIELDS and str(v).strip()}
    key_env = os.environ.get("VIKING_LLM_API_KEY", "").strip()
    if key_env:
        values["llm_api_key"] = key_env
    regs = seed.get("registered")
    if isinstance(regs, list) and regs:
        values["registered"] = [r for r in regs if _valid_registered(r)]
    if not values:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(values, ensure_ascii=False, indent=2))
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return True


def _valid_registered(r) -> bool:
    return (isinstance(r, dict)
            and _id_re.fullmatch(str(r.get("id", "")))
            and str(r.get("name", "")).strip()
            and str(r.get("base_url", "")).strip()
            and str(r.get("model", "")).strip())



def file_path() -> Path:
    from .config import config

    return config.data_dir / "models.json"


def _load_raw() -> dict:
    p = file_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load() -> dict:
    """파일의 웹 설정 (없거나 깨졌으면 {}). — FIELDS 만."""
    data = _load_raw()
    return {k: str(v).strip() for k, v in data.items() if k in FIELDS and str(v).strip()}


def save(values: dict) -> None:
    """웹 설정 저장. API 키 등이 0600 권한 파일에 들어갑니다. (registered 는 유지)"""
    data = {k: str(values.get(k, "")).strip() for k in FIELDS if str(values.get(k, "")).strip()}
    prev = _load_raw()
    if isinstance(prev.get("registered"), list) and prev["registered"]:
        data["registered"] = prev["registered"]
    p = file_path()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    try:
        p.chmod(0o600)
    except OSError:
        pass


# ── 커스텀 모델 등록 (registered) ────────────────────────────────────── #
def load_registered() -> list:
    regs = _load_raw().get("registered")
    if not isinstance(regs, list):
        return []
    out = []
    for r in regs:
        if not isinstance(r, dict) or not _id_re.fullmatch(str(r.get("id", ""))):
            continue
        out.append({
            "id": r["id"],
            "kind": r.get("kind") if r.get("kind") in ("llm", "embed") else "llm",
            "name": str(r.get("name", "")).strip(),
            "base_url": str(r.get("base_url", "")).strip(),
            "model": str(r.get("model", "")).strip(),
            "api_key": str(r.get("api_key", "")).strip(),
            "endpoint": r.get("endpoint") if r.get("endpoint") in ("auto", "direct") else "auto",
        })
    return [r for r in out if r["name"] and r["base_url"] and r["model"]]


def add_registered(kind: str, name: str, base_url: str, model: str,
                   api_key: str = "", endpoint: str = "auto") -> dict:
    """커스텀 모델 등록 — 기존 설정/키는 건드리지 않습니다."""
    entry = {
        "id": f"m{int(time.time())}",
        "kind": kind if kind in ("llm", "embed") else "llm",
        "name": name.strip(),
        "base_url": base_url.strip().rstrip("/"),
        "model": model.strip(),
        "api_key": api_key.strip(),
        "endpoint": endpoint if endpoint in ("auto", "direct") else "auto",
    }
    data = _load_raw()
    regs = data.get("registered")
    if not isinstance(regs, list):
        regs = []
    regs.append(entry)
    data["registered"] = regs
    p = file_path()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return entry


def delete_registered(rid: str) -> bool:
    """커스텀 모델 삭제. (등록만 지우고 현재 적용 설정은 그대로)"""
    if not _id_re.fullmatch(rid):
        return False
    data = _load_raw()
    regs = data.get("registered")
    if not isinstance(regs, list):
        return False
    nxt = [r for r in regs if not (isinstance(r, dict) and r.get("id") == rid)]
    if len(nxt) == len(regs):
        return False
    data["registered"] = nxt
    p = file_path()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return True


def apply_env(cfg) -> None:
    """config 의 LLM/임베딩 필드를 환경변수 기준으로 재계산. (테스트 격리·핫스왑용)"""
    env = os.environ
    cfg.llm_base_url = env.get("VIKING_LLM_BASE_URL", "").strip() or _DEF["llm_base_url"]
    cfg.llm_api_key = env.get("VIKING_LLM_API_KEY", "").strip()
    cfg.llm_model = env.get("VIKING_LLM_MODEL", "").strip() or _DEF["llm_model"]
    cfg.embed_base_url = env.get("VIKING_EMBED_BASE_URL", "").strip() or cfg.llm_base_url
    cfg.embed_api_key = env.get("VIKING_EMBED_API_KEY", "").strip()
    cfg.embed_model = env.get("VIKING_EMBED_MODEL", "").strip() or _DEF["embed_model"]


def apply_to(cfg) -> None:
    """환경변수 기준 위에 웹 설정(models.json)을 덮어씀: 웹 > env > 기본값."""
    apply_env(cfg)
    web = load()
    if web.get("llm_base_url"):
        cfg.llm_base_url = web["llm_base_url"]
    if web.get("llm_api_key"):
        cfg.llm_api_key = web["llm_api_key"]
    if web.get("llm_model"):
        cfg.llm_model = web["llm_model"]
    if web.get("embed_base_url"):
        cfg.embed_base_url = web["embed_base_url"]
    elif not os.environ.get("VIKING_EMBED_BASE_URL", "").strip():
        cfg.embed_base_url = cfg.llm_base_url  # 별도 지정 없으면 LLM과 동일 주소
    if web.get("embed_api_key"):
        cfg.embed_api_key = web["embed_api_key"]
    if web.get("embed_model"):
        cfg.embed_model = web["embed_model"]


def mask(key: str) -> str:
    """화면 표시용 마스킹 — 끝 4자리만 보입니다."""
    if not key:
        return ""
    return "••••••••" + key[-4:] if len(key) > 8 else "•" * len(key)