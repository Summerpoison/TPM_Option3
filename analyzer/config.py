"""Configuration. The API key is read from the environment and never logged."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://api.paulsjob.ai/dev/v1"


def load_dotenv(path: str = ".env") -> None:
    """Populate os.environ from a .env file, without overriding real env vars.

    Deliberately minimal so the tool keeps zero runtime dependencies.
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str

    @classmethod
    def from_env(cls, dotenv: str = ".env") -> "Settings":
        load_dotenv(dotenv)
        key = os.environ.get("PAULSJOB_API_KEY", "").strip()
        if not key:
            raise SystemExit(
                "PAULSJOB_API_KEY is not set.\n"
                "  Copy .env.example to .env and add your key, or export it in your shell.\n"
                "  The key is read from the environment only and is never written to output."
            )
        base_url = os.environ.get("PAULSJOB_BASE_URL", DEFAULT_BASE_URL).strip()
        return cls(api_key=key, base_url=require_https(base_url))


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def require_https(base_url: str) -> str:
    """Refuse a base URL that would send the API key in cleartext.

    Plain http is allowed only towards the local machine, for running against
    a stub server in tests.
    """
    parts = urlsplit(base_url)
    if parts.scheme == "https" and parts.netloc:
        return base_url
    if parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS:
        return base_url
    raise SystemExit(
        f"PAULSJOB_BASE_URL must start with https:// (got {base_url!r}).\n"
        "  The API key travels in a request header and must not be sent over plain http."
    )
