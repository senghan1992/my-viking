"""관리자 → 모델 설정 — LLM/임베딩을 대시보드에서 관리.

환경변수를 몰라도 여기서 키를 넣으면 됩니다. 저장 즉시 적용 (재시작 불필요).
저장소: data/models.json (0600) — 우선순위 웹 설정 > 환경변수 > 기본값.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from .. import model_settings
from ..config import config
from ..deps import admin_required
from ..engine import llm

router = APIRouter(tags=["admin_models"])

_PROVIDERS_HINT = (
    "OpenAI https://api.openai.com/v1 · DeepSeek https://api.deepseek.com/v1 · "
    "Groq https://api.groq.com/openai/v1 · Volcengine ARK https://ark.cn-beijing.volces.com/api/v3 · "
    "Ollama(로컬) http://localhost:11434/v1"
)


def _flash(url: str, msg: str = "") -> str:
    return f"{url}?msg={msg}" if msg else url


def _validate_base(v: str, label: str) -> str | None:
    v = v.strip()
    if v and not (v.startswith("http://") or v.startswith("https://")):
        return f"{label} 주소는 http(s):// 로 시작해야 합니다."
    return None


def _source(web_val: str, env_key: str) -> str:
    if web_val:
        return "웹 설정"
    if os.environ.get(env_key, "").strip():
        return "환경변수"
    return "기본값"


@router.get("/admin/models")
def models_page(request: Request, user: dict = Depends(admin_required), msg: str = ""):
    web = model_settings.load()
    env = os.environ
    status = [
        {"label": "LLM 주소", "value": config.llm_base_url,
         "src": _source(web.get("llm_base_url", ""), "VIKING_LLM_BASE_URL")},
        {"label": "LLM 모델", "value": config.llm_model,
         "src": _source(web.get("llm_model", ""), "VIKING_LLM_MODEL")},
        {"label": "임베딩 주소", "value": config.embed_base_url,
         "src": ("LLM과 동일" if not web.get("embed_base_url") and not env.get("VIKING_EMBED_BASE_URL", "").strip()
                 else _source(web.get("embed_base_url", ""), "VIKING_EMBED_BASE_URL"))},
        {"label": "임베딩 모델", "value": config.embed_model,
         "src": _source(web.get("embed_model", ""), "VIKING_EMBED_MODEL")},
    ]
    keys = {
        "llm": {"set": bool(config.llm_api_key), "masked": model_settings.mask(config.llm_api_key),
                "src": ("웹 설정" if web.get("llm_api_key") else
                        ("환경변수" if env.get("VIKING_LLM_API_KEY", "").strip() else "미설정"))},
        "embed": {"set": bool(config.embed_api_key), "masked": model_settings.mask(config.embed_api_key),
                  "src": ("웹 설정" if web.get("embed_api_key") else
                          ("환경변수" if env.get("VIKING_EMBED_API_KEY", "").strip() else "미설정"))},
    }
    return request.app.state.templates.TemplateResponse(
        request, "admin_models.html",
        {"request": request, "user": user, "msg": msg, "status": status, "keys": keys,
         "form": {"llm_base_url": config.llm_base_url, "llm_model": config.llm_model,
                  "embed_base_url": config.embed_base_url, "embed_model": config.embed_model},
         "placeholders": {"llm": model_settings.mask(config.llm_api_key) or "API 키 입력",
                          "embed": model_settings.mask(config.embed_api_key) or "API 키 입력"},
         "providers_hint": _PROVIDERS_HINT},
    )


@router.post("/admin/models")
def models_save(user: dict = Depends(admin_required),
                llm_base_url: str = Form(""), llm_api_key: str = Form(""),
                llm_model: str = Form(""), llm_clear_key: str = Form(""),
                embed_base_url: str = Form(""), embed_api_key: str = Form(""),
                embed_model: str = Form(""), embed_clear_key: str = Form("")):
    err = _validate_base(llm_base_url, "LLM") or _validate_base(embed_base_url, "임베딩")
    if err:
        return RedirectResponse(_flash("/admin/models", err), status_code=303)

    web = model_settings.load()
    if llm_base_url.strip():
        web["llm_base_url"] = llm_base_url.strip().rstrip("/")
    if llm_model.strip():
        web["llm_model"] = llm_model.strip()
    if llm_clear_key == "on":
        web.pop("llm_api_key", None)
    elif llm_api_key.strip() and not llm_api_key.strip().startswith("•"):
        web["llm_api_key"] = llm_api_key.strip()  # 마스킹 값 그대로면 기존 키 유지

    if embed_base_url.strip():
        web["embed_base_url"] = embed_base_url.strip().rstrip("/")
    if embed_model.strip():
        web["embed_model"] = embed_model.strip()
    if embed_clear_key == "on":
        web.pop("embed_api_key", None)
    elif embed_api_key.strip() and not embed_api_key.strip().startswith("•"):
        web["embed_api_key"] = embed_api_key.strip()

    model_settings.save(web)
    model_settings.apply_to(config)  # 핫스왑 — 재시작 없이 즉시 적용
    return RedirectResponse(_flash("/admin/models", "모델 설정을 저장했습니다. 지금부터 적용됩니다."), status_code=303)


@router.post("/admin/models/test")
def models_test(user: dict = Depends(admin_required)):
    ok_l, err_l = llm.test_llm()
    ok_e, err_e = llm.test_embed()
    parts = [
        f"LLM {'✓ ' if ok_l else '✗ '}({err_l or '연결됨'})",
        f"임베딩 {'✓ ' if ok_e else '✗ '}({err_e or '연결됨'})",
    ]
    return RedirectResponse(_flash("/admin/models", "연결 테스트: " + " · ".join(parts)), status_code=303)


@router.post("/admin/models/reindex")
def models_reindex(user: dict = Depends(admin_required)):
    n = llm.reindex_all()
    if n:
        msg = f"전체 재색인 완료 — 지식 {n}권에 임베딩을 생성했습니다."
    else:
        msg = "임베딩이 설정되어 있지 않아 재색인하지 않았습니다. (지식은 키워드 검색으로 계속 동작)"
    return RedirectResponse(_flash("/admin/models", msg), status_code=303)