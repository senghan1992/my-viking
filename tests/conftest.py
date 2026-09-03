import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis import Config, Jarvis  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "jarvis"
    monkeypatch.setenv("JARVIS_HOME", str(h))
    # Config.load honours provider variables; a developer's shell must not
    # switch the suite onto a paid model or a remote embedding endpoint.
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ARK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    for var in list(__import__("os").environ):
        if var.startswith(("JARVIS_LLM_", "JARVIS_EMBED_")):
            monkeypatch.delenv(var, raising=False)
    return h


@pytest.fixture()
def jarvis(home):
    cfg = Config(home=home)
    cfg.save()
    return Jarvis(config=cfg)


@pytest.fixture()
def coding(jarvis):
    jarvis.init_project("app", template="coding", description="테스트 프로젝트")
    return jarvis
