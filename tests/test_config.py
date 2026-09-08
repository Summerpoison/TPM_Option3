"""Configuration guards."""
import pytest

from analyzer.config import Settings, require_https


class TestRequireHttps:
    def test_https_passes(self):
        assert require_https("https://api.paulsjob.ai/dev/v1") == "https://api.paulsjob.ai/dev/v1"

    @pytest.mark.parametrize("url", ["http://localhost:8000/v1", "http://127.0.0.1/v1"])
    def test_loopback_http_passes(self, url):
        assert require_https(url) == url

    @pytest.mark.parametrize("url", ["http://api.paulsjob.ai/dev/v1", "api.paulsjob.ai", "ftp://x", ""])
    def test_cleartext_or_malformed_is_refused(self, url):
        with pytest.raises(SystemExit) as info:
            require_https(url)
        assert "https://" in str(info.value)


class TestSettings:
    def test_from_env_refuses_plain_http(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PAULSJOB_API_KEY", "k")
        monkeypatch.setenv("PAULSJOB_BASE_URL", "http://api.paulsjob.ai/dev/v1")
        with pytest.raises(SystemExit):
            Settings.from_env(dotenv=str(tmp_path / "none"))

    def test_from_env_reads_key_and_url(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PAULSJOB_API_KEY", "k")
        monkeypatch.setenv("PAULSJOB_BASE_URL", "https://example.test/v1")
        settings = Settings.from_env(dotenv=str(tmp_path / "none"))
        assert settings.api_key == "k" and settings.base_url == "https://example.test/v1"
