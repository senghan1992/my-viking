"""OpenAI 호환 게이트웨이(Databricks Serving 등)로의 요청 경로/응답 처리.

pi 가 쓰는 것과 같은 Databricks 엔드포인트는 base_url 이 곧 엔드포인트
(…/invocations)라서 그 뒤에 /chat/completions 를 붙이면 404 가 난다.
path="/" 로 그 자체를 맞추고, reasoner 모델의 빈 content 는
reasoning_content 로 메운다.
"""

import httpx

from jarvis.config import LLMConfig
from jarvis.llm import LLM


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _llm(**cfg):
    cfg.setdefault("base_url", "https://gw.example.com/v1")
    return LLM(LLMConfig(provider="openai", model="m", **cfg), api_key="k")


def test_default_path_appends_chat_completions(monkeypatch):
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        body = kw["json"]
        assert body["model"] == "m"
        assert body["messages"][-1]["content"] == "prompt"
        return _FakeResp({"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    monkeypatch.setattr(httpx, "post", fake_post)
    res = _llm().complete("prompt")
    assert seen["url"] == "https://gw.example.com/v1/chat/completions"
    assert res.text == "hi" and res.ok


def test_slash_path_posts_to_base_itself(monkeypatch):
    """Databricks Serving: …/invocations 가 곧 chat 경로. /chat/completions 를 붙이면 404."""
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        return _FakeResp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    cfg = dict(base_url="https://lge.example.com/serving-endpoints/deepseek/invocations", path="/")
    res = _llm(**cfg).complete("prompt")
    assert seen["url"] == "https://lge.example.com/serving-endpoints/deepseek/invocations"
    assert res.text == "ok"


def test_custom_path_is_appended(monkeypatch):
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        return _FakeResp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    _llm(path="/custom").complete("p")
    assert seen["url"] == "https://gw.example.com/v1/custom"


def test_empty_content_falls_back_to_reasoning(monkeypatch):
    """DeepSeek reasoner: max_tokens 를 사고에 다 쓰면 content 가 비어 있다."""

    def fake_post(url, **kw):
        return _FakeResp(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "답을 생각하는 중…",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    res = _llm().complete("p")
    assert res.text == "답을 생각하는 중…"