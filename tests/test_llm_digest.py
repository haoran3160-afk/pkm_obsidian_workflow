import llm_digest


def test_can_generate_digest_copy_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("PKM_ENABLE_LLM_DIGEST_COPY", raising=False)

    assert llm_digest.can_generate_digest_copy() is False


def test_can_generate_digest_copy_accepts_enabled_flag(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PKM_ENABLE_LLM_DIGEST_COPY", "1")

    assert llm_digest.can_generate_digest_copy() is True
