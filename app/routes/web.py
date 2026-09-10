"""웹 페이지 — 로그인·가입·대시보드·관리자."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from .. import db
from ..config import config
from ..deps import admin_required, current_user, login_required
from ..security import hash_password, sign_session, verify_password

router = APIRouter(tags=["web"])


def _flash(url: str, msg: str = "") -> str:
    return f"{url}?msg={msg}" if msg else url


# ── 인증 페이지 ─────────────────────────────────────── #
@router.get("/login")
def login_page(request: Request, msg: str = ""):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return request.app.state.templates.TemplateResponse(request, "login.html", {"request": request, "msg": msg, "allow_signup": config.allow_signup}
    )


@router.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form("")):
    email = email.strip().lower()
    user = db.one("SELECT * FROM users WHERE email=?", (email,))
    if not user or not verify_password(password, user["pw_hash"]):
        return RedirectResponse(_flash("/login", "이메일 또는 비밀번호가 틀렸습니다."), status_code=303)
    if user["disabled"]:
        return RedirectResponse(_flash("/login", "비활성화된 계정입니다. 관리자에게 문의하세요."), status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("viking_session", sign_session(user["id"]), httponly=True, max_age=30 * 24 * 3600, samesite="lax")
    return resp


@router.get("/signup")
def signup_page(request: Request, msg: str = ""):
    if not config.allow_signup:
        raise HTTPException(403, "새 가입이 닫혀 있습니다.")
    return request.app.state.templates.TemplateResponse(request, "signup.html", {"request": request, "msg": msg}
    )


@router.post("/signup")
def signup(request: Request, name: str = Form(""), email: str = Form(""), password: str = Form("")):
    if not config.allow_signup:
        raise HTTPException(403, "새 가입이 닫혀 있습니다.")
    name = name.strip()[:40]
    email = email.strip().lower()
    if not name or not email or len(password) < 6:
        return RedirectResponse(_flash("/signup", "이름·이메일·6자 이상 비밀번호가 필요합니다."), status_code=303)
    if db.one("SELECT id FROM users WHERE email=?", (email,)):
        return RedirectResponse(_flash("/signup", "이미 가입된 이메일입니다."), status_code=303)

    is_first = db.one("SELECT COUNT(*) AS n FROM users")["n"] == 0
    role = "admin" if (config.first_user_admin and is_first) or email in config.admin_emails else "user"
    uid = db.execute(
        "INSERT INTO users(email, name, pw_hash, role, created_at) VALUES(?,?,?,?,?)",
        (email, name, hash_password(password), role, db.now()),
    )
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("viking_session", sign_session(uid), httponly=True, max_age=30 * 24 * 3600, samesite="lax")
    return resp


_INSTALL_SH = r"""#!/usr/bin/env bash
# myviking 자동 연결 (install.sh) — jv 설치(없으면) + 이 폴더를 프로젝트에 연결 + 이 머신의 에이전트 전부 설치
# 사용법: curl -fsSL __BASE__/install.sh | bash -s -- --url __BASE__ --key jv_... --project <슬러그>
set -u
URL=""; KEY=""; PROJECT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --url) URL="${2:-}"; shift 2;;
    --key) KEY="${2:-}"; shift 2;;
    --project) PROJECT="${2:-}"; shift 2;;
    *) echo "알 수 없는 인자: $1"; exit 2;;
  esac
done
if [ -z "$URL" ] || [ -z "$KEY" ]; then
  echo "사용법: curl -fsSL __BASE__/install.sh | bash -s -- --url __BASE__ --key jv_... --project <슬러그>"
  exit 2
fi

if ! command -v jv >/dev/null 2>&1; then
  echo "► jv 설치 중 (python3 pip, 잠시 기다리세요)..."
  (python3 -m pip install --user --quiet git+https://github.com/senghan1992/my-viking.git \
    || python3 -m pip install --user --break-system-packages --quiet git+https://github.com/senghan1992/my-viking.git \
    || python3 -m pip install --quiet git+https://github.com/senghan1992/my-viking.git) \
    && echo "✓ jv 설치 완료" || echo "⚠ jv 설치가 실패했습니다 — 아래에서 계속 시도합니다."
fi
# pyenv / --user 설치 경로를 PATH 에 보충 (재부팅에도 살아있는 jv 인지 확인)
export PATH="$HOME/.local/bin:$PATH"
if ! command -v jv >/dev/null 2>&1; then
  for c in "$HOME"/.pyenv/versions/*/bin/jv; do
    [ -x "$c" ] && { export PATH="$(dirname "$c"):$PATH"; break; }
  done
fi

if command -v jv >/dev/null 2>&1; then
  if [ -n "$PROJECT" ]; then
    jv connect --url "$URL" --key "$KEY" --project "$PROJECT"
  else
    jv connect --url "$URL" --key "$KEY"
  fi
else
  echo "⚠ jv 를 찾지 못했습니다 — python3/pip 가 설치되어 있는지 확인하고,"
  echo "  pip install git+https://github.com/senghan1992/my-viking.git 후 다시 시도하세요."
  exit 1
fi
"""


@router.get("/install.sh")
def install_script(request: Request):
    """에이전트 머신용 자동 연결 스크립트 — 키는 포함하지 않고 사용자가 argv 로 넘긴다."""
    base = _install_base_url(request)
    return PlainTextResponse(
        _INSTALL_SH.replace("__BASE__", base),
        media_type="text/x-shellscript; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="install.sh"'},
    )


def _install_base_url(request: Request) -> str:
    from ..config import config as _config
    if _config.base_url:
        return _config.base_url.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("viking_session")
    return resp


# ── 대시보드 (내 도서관) ──────────────────────────────── #
@router.get("/")
def dashboard(request: Request, user: dict = Depends(login_required), msg: str = ""):
    projects = db.rows(
        """SELECT p.*,
                  (SELECT COUNT(*) FROM memories m WHERE m.project_id=p.id AND m.status!='superseded') AS memory_count,
                  (SELECT COUNT(*) FROM sessions s WHERE s.project_id=p.id) AS session_count
           FROM projects p WHERE p.user_id=? ORDER BY p.updated_at DESC""",
        (user["id"],),
    )
    return request.app.state.templates.TemplateResponse(
        request, "dashboard.html",
        {"request": request, "user": user, "projects": projects, "msg": msg,
         "stats": db.one(
             "SELECT COUNT(DISTINCT m.project_id) AS project_count,"
             "       COUNT(*) AS memory_count,"
             "       SUM(CASE WHEN m.status='established' THEN 1 ELSE 0 END) AS estab_count,"
             "       (SELECT COUNT(*) FROM sessions s JOIN projects p ON p.id=s.project_id"
             "         WHERE p.user_id=?) AS session_count"
             "  FROM memories m JOIN projects p ON p.id=m.project_id"
             " WHERE p.user_id=? AND m.status!='superseded'",
             (user["id"], user["id"]),
         ) or {},
         "has_any_session": any(p["session_count"] > 0 for p in projects),
        },
    )


# ── 관리자 ───────────────────────────────────────────── #
@router.get("/admin")
def admin_page(request: Request, user: dict = Depends(admin_required), msg: str = ""):
    users = db.rows(
        """SELECT u.*,
                  (SELECT COUNT(*) FROM projects p WHERE p.user_id=u.id) AS project_count,
                  (SELECT COUNT(*) FROM memories m JOIN projects p ON p.id=m.project_id
                    WHERE p.user_id=u.id AND m.status!='superseded') AS memory_count
           FROM users u ORDER BY u.created_at""",
    )
    stats = db.one(
        "SELECT (SELECT COUNT(*) FROM users) AS users, (SELECT COUNT(*) FROM projects) AS projects,"
        " (SELECT COUNT(*) FROM memories WHERE status!='superseded') AS memories,"
        " (SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL) AS keys"
    )
    return request.app.state.templates.TemplateResponse(request, "admin.html", {"request": request, "user": user, "users": users, "stats": stats, "msg": msg},
    )


@router.post("/admin/users/{uid}/toggle")
def toggle_user(uid: int, user: dict = Depends(admin_required)):
    target = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not target:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    if target["id"] == user["id"]:
        return RedirectResponse(_flash("/admin", "자기 자신은 비활성화할 수 없습니다."), status_code=303)
    db.execute("UPDATE users SET disabled=? WHERE id=?", (0 if target["disabled"] else 1, uid))
    return RedirectResponse("/admin", status_code=303)