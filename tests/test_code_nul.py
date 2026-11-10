"""NUL bytes in source files must not reach Postgres: text fields reject them and the
crawl of that repo would fail (seen on real repos in the 5,000-repo corpus)."""
from homegrown.extract.code import code_words, extract


def test_extract_drops_nul_bytes():
    src = "def get_user\x00_token(x):\n    \"\"\"Return the\x00 token.\"\"\"\n"
    out = extract(src)
    assert "\x00" not in out
    assert "get_user_token" in out


def test_code_words_never_returns_nul_bytes():
    out = code_words("class Http\x00Retry:\nretry_backoff\x00")
    assert "\x00" not in out
    assert "retry backoff" in out
