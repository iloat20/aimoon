"""Unified configuration via Pydantic Settings, loaded from .env / env vars."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# 已知可用的 DeepSeek 公开模型。其它名称(如历史占位符)在官方 API 会返回 400,
# 触发整条重试 + 静默降级,既浪费 token 又严重拉低报告质量。
# 已知可用的 DeepSeek 模型。其它名称(如历史占位符)在官方 API 会返回 400,
# 触发整条重试 + 静默降级,既浪费 token 又严重拉低报告质量。
# 注: "deepseek-v4-flash" 非官方公开名,多为网关对 reasoner 的别名重命名,
# 本环境以此名暴露推理模型,故列入已知集合以避免误告警。
_KNOWN_DEEPSEEK_MODELS = frozenset({"deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash"})


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
    # 注意: 旧默认 "deepseek-v4-flash" 不是公开 DeepSeek 模型,会导致 API 400 +
    # 整条重试 + 静默降级(高成本 + 低质量)。如用思考模型请显式设置 DEEPSEEK_MODEL。
    deepseek_model: str = "deepseek-reasoner"
    deepseek_max_tokens: int = 16384
    deepseek_temperature: float = 0.3

    # 成本杠杆: ANALYSIS 阶段是 reasoner 思考 token 的主要消耗点(思考 token 按输出计费)。
    # 默认 "high"(最深推理,质量最高); 设为 "medium"/"low" 可直降思考 token 约 50%+。
    # COMPILE 阶段已固定用 "medium"(见 ai/pipeline/orchestrator.py)。
    deepseek_analysis_effort: str = "high"
    # 成本杠杆: ANALYSIS 阶段输出 token 上限。骨架 JSON 比旧初稿短得多
    # (800-1200 token vs 2500-3500 token),4096 已留充足余量。
    deepseek_analysis_max_tokens: int = 4096

    # 推理能力开关: 是否向 API 发送 reasoning_effort。
    # None(默认) = 按模型名自动判断(含 "reasoner" 子串则发);
    # True  = 强制发送(用于被重命名的推理模型,如某些网关把 reasoner 暴露为
    #         "deepseek-v4-flash" 等非标准名,否则 effort 会被静默丢弃);
    # False = 永不发送(纯 chat/flash 模型,传该参数会被 API 拒绝)。
    deepseek_reasoner_enabled: bool | None = None

    # 成本开关: 是否启用股吧 Playwright 渲染(重算力)。默认 False = 先用轻量
    # HTML 抓取,仅在 HTML 为空/被 WAF 时才升级到浏览器。设 True 可恢复旧行为。
    guba_playwright_enabled: bool = False

    # 成本开关: 是否启用 K 线第四级回退(东方财富 push2his 直连)。
    # 若该子域在部署网络被 WAF 拦截(连接重置),建议设 False 以省去约 14s 空耗重试。
    kline_eastmoney_direct_enabled: bool = True

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

    @model_validator(mode="after")
    def _warn_unknown_model(self) -> Settings:
        """启动期可见性: 模型名非已知公开模型时告警(不阻断运行)。"""
        if self.deepseek_model and self.deepseek_model not in _KNOWN_DEEPSEEK_MODELS:
            logger.warning(
                "[settings] deepseek_model=%r 不是已知 DeepSeek 公开模型(%s)。"
                "若 API 返回 400 将触发整条重试并静默降级,既浪费 token 又拉低报告质量;"
                "请通过 DEEPSEEK_MODEL 环境变量设置为正确模型。",
                self.deepseek_model,
                ", ".join(sorted(_KNOWN_DEEPSEEK_MODELS)),
            )
        _valid_efforts = {"low", "medium", "high", "max"}
        if self.deepseek_analysis_effort and self.deepseek_analysis_effort not in _valid_efforts:
            logger.warning(
                "[settings] deepseek_analysis_effort=%r 不在允许集合 %s,将被原样发给 API"
                "(非 reasoner 模型会忽略,reasoner 模型可能报错)。",
                self.deepseek_analysis_effort,
                ", ".join(sorted(_valid_efforts)),
            )
        return self


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
