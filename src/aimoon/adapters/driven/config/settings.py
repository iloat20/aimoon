"""Unified configuration via Pydantic Settings, loaded from .env / env vars."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


def _find_project_root() -> Path:
    """Walk up from this file to find pyproject.toml, fallback to parents[5]."""
    p = Path(__file__).resolve().parent
    for _ in range(10):
        if (p / "pyproject.toml").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return Path(__file__).resolve().parents[5]


_ENV_FILE = _find_project_root() / ".env"


class Settings(BaseSettings):
    """Application settings with .env support."""

    model_config = {
        "env_file": str(_ENV_FILE),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_max_tokens: int = 16384
    deepseek_temperature: float = 0.3

    # 东方财富数据(akshare)的代理补丁配置。
    # 配置后 akshare-proxy-patch 会注入代理认证头,绕过部分 WAF 限制。
    # 留空则不走代理(默认)。代理 IP 需向商业代理服务商(芝麻/快代理等)购买。
    akshare_proxy_auth_ip: str = ""
    akshare_proxy_auth_token: str = ""

    xueqiu_cookie: str = ""
    xueqiu_token: str = ""

    mock_mode: bool = False

    cache_dir: str = "./cache"
    financial_report_cache_days: int = 30

    output_dir: str = "./output"

    default_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    @property
    def project_root(self) -> Path:
        return _find_project_root()

    @property
    def cache_path(self) -> Path:
        p = Path(self.cache_dir)
        if not p.is_absolute():
            p = self.project_root / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir)
        if not p.is_absolute():
            p = self.project_root / p
        p.mkdir(parents=True, exist_ok=True)
        return p


_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the singleton Settings instance.

    For testing, call inject_settings() first to provide an explicit instance.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def inject_settings(settings: Settings) -> None:
    """Inject a Settings instance for testing or explicit configuration.

    Usage:
        test_settings = Settings(deepseek_api_key="test-key", mock_mode=True)
        inject_settings(test_settings)

    When injected, get_settings() returns the provided instance directly,
    bypassing .env file loading.
    """
    global _settings
    _settings = settings


def reset_settings() -> None:
    """Reset the singleton (useful for test teardown)."""
    global _settings
    _settings = None
