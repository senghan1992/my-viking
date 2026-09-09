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

_DEFAULTS = {
    "llm_base_url": "https://api.openai.com/v1",
    "llm_model": "gpt-4o-mini",
    "embed_model": "text-embedding-3-small",
}


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
    cfg.llm_base_url = env.get("VIKING_LLM_BASE_URL", "").strip() or _DEFAULTS["llm_base_url"]
    cfg.llm_api_key = env.get("VIKING_LLM_API_KEY", "").strip()
    cfg.llm_model = env.get("VIKING_LLM_MODEL", "").strip() or _DEFAULTS["llm_model"]
    cfg.embed_base_url = env.get("VIKING_EMBED_BASE_URL", "").strip() or cfg.llm_base_url
    cfg.embed_api_key = env.get("VIKING_EMBED_API_KEY", "").strip()
    cfg.embed_model = env.get("VIKING_EMBED_MODEL", "").strip() or _DEFAULTS["embed_model"]


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