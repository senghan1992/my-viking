"""Provider settings from the environment — the Docker deployment has no
``jv config`` step, and /health used to recommend variables that did not exist."""

from jarvis.config import Config
from jarvis.embed import Embedder


def test_anthropic_key_alone_turns_the_llm_on(home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg = Config.load(home)
    assert cfg.llm.provider == "anthropic"
    assert cfg.api_key("llm") == "sk-ant-test"


def test_explicit_llm_provider_wins_over_the_anthropic_shortcut(home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "gpt-x")
    cfg = Config.load(home)
    assert (cfg.llm.provider, cfg.llm.model, cfg.llm.api_key_env) == ("openai", "gpt-x", "OPENAI_API_KEY")


def test_embed_provider_gets_model_dim_and_key_env_defaults(home, monkeypatch):
    monkeypatch.setenv("JARVIS_EMBED_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = Config.load(home)
    assert cfg.embed.model == "text-embedding-3-small"
    assert cfg.embed.dim == 1536
    assert cfg.embed.api_key_env == "OPENAI_API_KEY"
    assert Embedder.from_config(cfg).configured_semantic is True


def test_embed_provider_without_its_key_is_reported_as_hashing(home, monkeypatch):
    monkeypatch.setenv("JARVIS_EMBED_PROVIDER", "openai")
    cfg = Config.load(home)
    emb = Embedder.from_config(cfg)
    assert emb.semantic is False
    assert len(emb.embed("x")) == cfg.embed.dim  # fallback keeps the configured length


def test_ollama_needs_no_key(home, monkeypatch):
    monkeypatch.setenv("JARVIS_EMBED_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_EMBED_BASE_URL", "http://host.docker.internal:11434/v1")
    cfg = Config.load(home)
    assert cfg.embed.model == "bge-m3" and cfg.embed.dim == 1024
    assert Embedder.from_config(cfg).configured_semantic is True


def test_unreachable_endpoint_is_reported_not_hidden(home, monkeypatch):
    """Configured is not in use: with the endpoint down the store fills with
    hashing vectors, and /health must not call that "최상"."""
    monkeypatch.setenv("JARVIS_EMBED_PROVIDER", "openai")
    monkeypatch.setenv("JARVIS_EMBED_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = Config.load(home)
    emb = Embedder.from_config(cfg)
    assert emb.configured_semantic is True
    assert emb.semantic is False
    assert emb.probe_error
    assert emb.similarity_floor == 0.0
    assert len(emb.embed("x")) == cfg.embed.dim
    assert emb.fallbacks == 1


def test_config_set_does_not_bake_env_overrides_into_the_file(home, monkeypatch):
    monkeypatch.setenv("JARVIS_EMBED_PROVIDER", "openai")
    Config(home=home).save()
    loaded = Config.load(home)
    assert loaded.embed.provider == "openai"
    on_disk = Config.load(home, env=False)
    assert on_disk.embed.provider == "hashing"
    assert Config.load(home, env=False).embed.dim == 512


def test_unknown_model_keeps_explicit_dim(home, monkeypatch):
    monkeypatch.setenv("JARVIS_EMBED_PROVIDER", "volcengine")
    monkeypatch.setenv("JARVIS_EMBED_MODEL", "doubao-embedding-x")
    monkeypatch.setenv("JARVIS_EMBED_DIM", "2560")
    cfg = Config.load(home)
    assert cfg.embed.dim == 2560 and cfg.embed.api_key_env == "ARK_API_KEY"


def test_saved_config_still_loads_without_env(home):
    cfg = Config(home=home)
    cfg.save()
    again = Config.load(home)
    assert again.llm.provider == "none" and again.embed.provider == "hashing"
