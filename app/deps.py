"""FastAPI 의존성 — 웹 세션 사용자 / 에이전트 API 키 인증."""
from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from . import db
from .security import hash_api_key, verify_session


def current_user(request: Request) -> dict | None:
    """쿠키 세션 → 사용자. 없거나 비활성이면 None."""
    token = request.cookies.get("viking_session")
    if not token:
        return None
    uid = verify_session(token)
    if not uid:
        return None
    user = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if user and not user["disabled"]:
        return user
    return None


def login_required(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def admin_required(request: Request):
    user = current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "관리자만 접근할 수 있습니다.")
    return user


def bearer_auth(authorization: str | None = Header(default=None)) -> dict:
    """Agent API 용 — Bearer jv_ 키를 프로젝트로 해석."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer API 키가 필요합니다.")
    raw = authorization[7:].strip()
    digest = hash_api_key(raw)
    key = db.one(
        """SELECT k.*, p.slug, p.name AS project_name, p.user_id
           FROM api_keys k JOIN projects p ON p.id = k.project_id
           WHERE k.key_hash=? AND k.revoked_at IS NULL""",
        (digest,),
    )
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "키가 유효하지 않거나 폐기되었습니다.")
    return key


def require_owner(project: dict, user: dict | None) -> dict:
    """프로젝트 소유권 확인 (관리자 포함)."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    if project["user_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "이 프로젝트의 주인만 접근할 수 있습니다.")
    return user


def get_project_or_404(slug: str) -> dict:
    project = db.one("SELECT * FROM projects WHERE slug=?", (slug,))
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "프로젝트를 찾을 수 없습니다.")
    return project