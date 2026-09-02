from jarvis.tokens import estimate_tokens, truncate_to_tokens


def test_empty():
    assert estimate_tokens("") == 0


def test_korean_costs_more_than_naive_quarter_rule():
    """Korean must not be counted as len/4, or every saving report lies."""
    text = "안녕하세요 이것은 한글 문장입니다"
    naive = len(text) / 4
    assert estimate_tokens(text) > naive


def test_latin_roughly_quarter_length():
    text = "the quick brown fox jumps over the lazy dog"
    est = estimate_tokens(text)
    assert 8 <= est <= 16


def test_monotonic():
    a = "짧은 문장"
    assert estimate_tokens(a) < estimate_tokens(a * 5)


def test_truncate_respects_limit():
    text = "가나다라마바사아자차카타파하 " * 40
    out = truncate_to_tokens(text, 30)
    assert estimate_tokens(out) <= 30
    assert out and text.startswith(out[: len(out) - 1])


def test_truncate_noop_when_short():
    assert truncate_to_tokens("짧다", 100) == "짧다"


def test_truncate_zero():
    assert truncate_to_tokens("무엇이든", 0) == ""
