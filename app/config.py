"""환경 변수 설정.

모든 설정은 환경 변수 하나로 끝납니다. docker compose / Portainer 스택에서
고치면 됩니다. (루트 docker-compose.yml 참고)
"""
from __future__ import annotations

import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


class Config:
    def __init__(self) -> None:
        # 데이터 디렉터리 — SQLite DB + 자동 생성된 서명 시크릿이 여기 저장됩니다.
        self.data_dir = Path(_env("VIKING_DATA", "/data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 세션 쿠키 서명용 시크릿. 없으면 data_dir 에 생성해 보관(재시작에도 유지).
        self.secret = _env("VIKING_SECRET") or self._load_or_create_secret()

        # 서버가 외부에서 보이는 주소 (연결 가이드에 표시용). 비우면 요청 Host 사용.
        self.base_url = _env("VIKING_BASE_URL", "").rstrip("/")

        # 사람들 스스로 가입 가능 여부. 운영 중 막으려면 false.
        self.allow_signup = _env("VIKING_ALLOW_SIGNUP", "true").lower() == "true"

        # 첫 가입자가 자동으로 관리자가 되지 않게 하려면... 기본은 자동 승격(true).
        self.first_user_admin = _env("VIKING_FIRST_USER_ADMIN", "true").lower() == "true"

        # 관리자를 특정 이메일로 고정하고 싶을 때 (여러 명은 쉼표 구분).
        self.admin_emails = {
            e.strip().lower() for e in _env("VIKING_ADMIN_EMAILS").split(",") if e.strip()
        }

        # ── 선택: LLM / 임베딩 (없어도 전부 동작. 있으면 요약·검색 품질이 좋아짐) ──
        # OpenAI 호환 API (OpenAI / Groq / DeepSeek / vLLM / Ollama / Volcengine ARK ...)
        self.llm_base_url = _env("VIKING_LLM_BASE_URL", "https://api.openai.com/v1")
        self.llm_api_key = _env("VIKING_LLM_API_KEY")
        self.llm_model = _env("VIKING_LLM_MODEL", "gpt-4o-mini")
        self.embed_base_url = _env("VIKING_EMBED_BASE_URL", self.llm_base_url)
        self.embed_api_key = _env("VIKING_EMBED_API_KEY", self.llm_api_key)
        self.embed_model = _env("VIKING_EMBED_MODEL", "text-embedding-3-small")

        self.port = int(_env("VIKING_PORT", "8787"))

    # ------------------------------------------------------------------ #
    def _load_or_create_secret(self) -> str:
        path = self.data_dir / "SECRET_KEY"
        if path.exists():
            return path.read_text().strip()
        import secrets

        value = secrets.token_hex(32)
        path.write_text(value)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return value


config = Config()