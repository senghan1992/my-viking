import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis import Config, Jarvis  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "jarvis"
    monkeypatch.setenv("JARVIS_HOME", str(h))
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
