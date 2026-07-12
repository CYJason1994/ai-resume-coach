from app.core.config import Settings, get_settings


def test_settings_loads():
    s = get_settings()
    assert s.APP_NAME
    assert s.LLM_CHAT_MODEL == "deepseek-v4-flash"
    assert s.LLM_CONCURRENCY == 6
    assert s.ACCESS_TOKEN_BYTES == 32


def test_csv_split():
    s = Settings(CORS_ORIGINS="a,b,c", JOB_SOURCE_CATEGORIES="x, y")
    assert s.CORS_ORIGINS == ["a", "b", "c"]
    assert s.JOB_SOURCE_CATEGORIES == ["x", "y"]
