"""사전 등록 모델 카탈로그 — 관리자 드롭다운에 바로 나오는 모델들.

피 보유자의 pi 커스텀 프로바이더(Databricks Serving)에서 가져온 목록:
  - 호스트: https://lge-esdatapf.cloud.databricks.com
  - 형식: OpenAI 호환 payload 를 `/serving-endpoints/<endpoint>/invocations` 에 그대로 POST
    (뒤에 /chat/completions 를 붙이면 404 — 엔드포인트 주소가 곧 완전한 경로)

키는 여기에 없습니다 — 관리자 화면에서 LLM 키 칸에 한 번 넣으면 됩니다
(data/models.json 0600 에 저장, 화면엔 마스킹). 호스트를 바꿔야 하면
DATABRICKS_HOST 상수를 수정하세요.
"""
from __future__ import annotations

DATABRICKS_HOST = "https://lge-esdatapf.cloud.databricks.com"

# (endpoint id, 표시 이름, 기본 선택 여부) — pi models.json 의 databricks 프로바이더
DATABRICKS_MODELS: list[tuple[str, str, bool]] = [
    ("databricks-claude-sonnet-5", "Claude Sonnet 5", False),
    ("databricks-claude-opus-4-8", "Claude Opus 4.8", False),
    ("databricks-deepseek-v4-flash-0731", "DeepSeek V4 Flash 0731", True),
    ("databricks-kimi-k3", "Kimi K3", False),
    ("databricks-glm-5-2", "GLM 5.2", False),
    ("databricks-claude-opus-5", "Claude Opus 5", False),
    ("databricks-glm-5-3-flash", "GLM 5.3 Flash", False),
    ("databricks-grok-4-6", "Grok 4.6", False),
    ("databricks-claude-fable-5", "Claude Fable 5", False),
    ("databricks-claude-fable-5-1", "Claude Fable 5.1", False),
    ("databricks-claude-haiku-4-5", "Claude Haiku 4.5", False),
    ("databricks-claude-opus-4-1", "Claude Opus 4.1", False),
    ("databricks-claude-opus-4-5", "Claude Opus 4.5", False),
    ("databricks-claude-opus-4-6", "Claude Opus 4.6", False),
    ("databricks-claude-opus-4-7", "Claude Opus 4.7", False),
    ("databricks-claude-sonnet-4-5", "Claude Sonnet 4.5", False),
    ("databricks-claude-sonnet-4-6", "Claude Sonnet 4.6", False),
    ("databricks-gemini-3-flash", "Gemini 3 Flash", False),
    ("databricks-gemini-3-1-flash-image", "Gemini 3.1 Flash Image", False),
    ("databricks-gemini-3-1-flash-lite", "Gemini 3.1 Flash Lite", False),
    ("databricks-gemini-3-1-pro", "Gemini 3.1 Pro", False),
    ("databricks-gemini-3-5-flash", "Gemini 3.5 Flash", False),
    ("databricks-gemini-3-5-flash-lite", "Gemini 3.5 Flash Lite", False),
    ("databricks-gemini-3-6-flash", "Gemini 3.6 Flash", False),
    ("databricks-gemini-3-7-flash", "Gemini 3.7 Flash", False),
    ("databricks-gemini-3-8-flash", "Gemini 3.8 Flash", False),
    ("databricks-gemini-3-pro-image", "Gemini 3 Pro Image", False),
    ("databricks-gpt-5", "GPT 5", False),
    ("databricks-gpt-5-1", "GPT 5.1", False),
    ("databricks-gpt-5-2", "GPT 5.2", False),
    ("databricks-gpt-5-4", "GPT 5.4", False),
    ("databricks-gpt-5-4-mini", "GPT 5.4 Mini", False),
    ("databricks-gpt-5-4-nano", "GPT 5.4 Nano", False),
    ("databricks-gpt-5-5", "GPT 5.5", False),
    ("databricks-gpt-5-mini", "GPT 5 Mini", False),
    ("databricks-gpt-5-nano", "GPT 5 Nano", False),
    ("databricks-gpt-5-6-luna", "GPT 5.6 Luna", False),
    ("databricks-gpt-5-6-sol", "GPT 5.6 Sol", False),
    ("databricks-gpt-5-6-terra", "GPT 5.6 Terra", False),
    ("databricks-gpt-6-astra", "GPT 6 Astra", False),
    ("databricks-gpt-oss-120b", "GPT-OSS 120B", False),
    ("databricks-gpt-oss-20b", "GPT-OSS 20B", False),
    ("databricks-deepseek-v4-pro-0813", "DeepSeek V4 Pro 0813", False),
    ("databricks-gemma-3-12b", "Gemma 3 12B", False),
    ("databricks-glm-5-3", "GLM 5.3", False),
    ("databricks-inkling", "Inkling", False),
    ("databricks-llama-4-maverick", "Llama 4 Maverick", False),
    ("databricks-meta-llama-3-1-8b-instruct", "Meta Llama 3.1 8B", False),
    ("databricks-meta-llama-3-3-70b-instruct", "Meta Llama 3.3 70B", False),
    ("databricks-qwen3-next-80b-a3b-instruct", "Qwen3 Next 80B", False),
    ("databricks-qwen35-122b-a10b", "Qwen3.5 122B", False),
]


def catalog() -> list[dict]:
    """드롭다운용 — {id, name, base_url, model, default, endpoint:\"direct\"}"""
    out = []
    for eid, name, default in DATABRICKS_MODELS:
        out.append({
            "id": eid,
            "name": name,
            "base_url": f"{DATABRICKS_HOST}/serving-endpoints/{eid}/invocations",
            "model": eid,
            "default": default,
            "endpoint": "direct",
        })
    return out