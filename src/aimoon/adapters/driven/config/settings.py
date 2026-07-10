"""Unified configuration via Pydantic Settings, loaded from .env / env vars."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# 已知可用的 DeepSeek 公开模型(官方 API 文档 2026-07 口径):
#   deepseek-v4-flash  —— 当前主模型,统一支持「非思考 + 思考」两种模式(思考为默认)
#   deepseek-v4-pro    —— 更强(3× 单价、并发 500),适合最难标的;本环境网关未开放
#   deepseek-chat / deepseek-reasoner —— 将于 2026/07/24 23:59 弃用
#       出于兼容,二者分别对应 v4-flash 的非思考 / 思考模式,可无缝迁移。
# 其它名称在官方 API 会返回 400,触发整条重试 + 静默降级,既浪费 token 又拉低质量。
_KNOWN_DEEPSEEK_MODELS = frozenset(
    {"deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"}
)
# 2026/07/24 弃用的旧模型名(仅用于弃用告警,不阻断运行)。
_DEPRECATED_DEEPSEEK_MODELS = frozenset({"deepseek-chat", "deepseek-reasoner"})


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
    # 默认主模型 = deepseek-v4-flash(官方当前模型,思考模式为默认)。
    # 旧默认 deepseek-reasoner 将于 2026/07/24 弃用,故迁移到 v4-flash。
    # 需要更强推理且预算充足时,可设 DEEPSEEK_MODEL=deepseek-v4-pro(3× 单价)。
    deepseek_model: str = "deepseek-v4-flash"
    # DIRECT 直出流(完整报告)的输出上限。模型总输出上限 384K,此值仅作安全天花板,
    # 模型写够即停、不会多耗 token;设 24576 确保最长 8 节报告不被截断
    # (思考 token + 正文均需计入 max_tokens)。
    deepseek_max_tokens: int = 24576
    deepseek_temperature: float = 0.3

    # 思考强度(仅思考模式生效)。官方只区分两档:high / max;
    # low / medium 会被 API 静默映射为 high,xhigh 映射为 max。
    # 默认 max(最深推理,质量最高;思考 token 按输出计价,是主要成本)。
    # 想降思考 token 成本可设为 high;想大幅省钱应直接关闭思考
    # (见 deepseek_thinking_enabled / COMPILE 阶段)。
    deepseek_analysis_effort: str = "max"
    # ANALYSIS 骨架 JSON 的输出上限。骨架比旧初稿短得多,4096 已留充足余量。
    deepseek_analysis_max_tokens: int = 4096

    # 思考模式开关(官方参数 thinking:{type:enabled/disabled},默认 enabled)。
    # None(默认)         = 不显式覆盖,沿用模型默认(对 v4-* 即 enabled);
    # True                = 强制开启思考 + 发送 reasoning_effort(质量最高,思考 token 按输出计费);
    # False               = 关闭思考(纯扩写/格式化场景),不再发 reasoning_effort,
    #                       改回传 temperature(top_p 等仍忽略)。
    # 注: 旧字段 deepseek_reasoner_enabled 为其兼容别名,未设置时回退读取之。
    deepseek_thinking_enabled: bool | None = None

    # 兼容别名: 旧环境以 DEEPSEEK_REASONER_ENABLED=true 强制开启思考。
    # 新代码优先读 deepseek_thinking_enabled;本字段保留以避免旧 .env 失效。
    deepseek_reasoner_enabled: bool | None = None

    # 成本开关: 是否启用股吧 Playwright 渲染(重算力)。默认 False = 先用轻量
    # HTML 抓取,仅在 HTML 为空/被 WAF 时才升级到浏览器。设 True 可恢复旧行为。
    guba_playwright_enabled: bool = False

    # 成本开关: 是否启用 K 线第四级回退(东方财富 push2his 直连)。
    # 若该子域在部署网络被 WAF 拦截(连接重置),建议设 False 以省去约 14s 空耗重试。
    kline_eastmoney_direct_enabled: bool = True

    # 质量护栏开关: DIRECT 直出流(完整报告)前是否做实时网络检索补充催化信息。
    # 默认 False = 控成本、不主动联网;设 True 可在写报告前抓最新催化,提升时效性。
    direct_web_search_enabled: bool = False

    # 质量护栏开关: 0-LLM 数字对账总开关(校验报告中的关键数字与源数据一致)。
    # 默认 True = 开启;设 False 可跳过对账(省 token,牺牲数字可靠性)。
    reconcile_enabled: bool = True

    # 质量护栏开关: 疑点非空时 LLM 定点重写的开关(针对对账/自查暴露的问题局部改写)。
    # 默认 True = 开启;设 False 可跳过定点重写(只暴露疑点、不做修正)。
    self_check_rewrite_enabled: bool = True

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
        """启动期可见性: 模型名非已知公开模型 / 已弃用 / effort 非法时告警(不阻断运行)。"""
        if self.deepseek_model and self.deepseek_model not in _KNOWN_DEEPSEEK_MODELS:
            logger.warning(
                "[settings] deepseek_model=%r 不是已知 DeepSeek 公开模型(%s)。"
                "若 API 返回 400 将触发整条重试并静默降级,既浪费 token 又拉低报告质量;"
                "请通过 DEEPSEEK_MODEL 环境变量设置为正确模型。",
                self.deepseek_model,
                ", ".join(sorted(_KNOWN_DEEPSEEK_MODELS)),
            )
        if self.deepseek_model in _DEPRECATED_DEEPSEEK_MODELS:
            logger.warning(
                "[settings] deepseek_model=%r 将于 2026/07/24 23:59 弃用,请迁移到 "
                "deepseek-v4-flash(思考模式,等价原 reasoner)或 deepseek-v4-pro。",
                self.deepseek_model,
            )
        # 官方仅 high/max 为有效档;low/medium 被静默映射为 high,xhigh 映射为 max。
        # 仍接受全部以兼容旧配置(不会报错),仅提示映射关系。
        _valid_efforts = {"low", "medium", "high", "max", "xhigh"}
        if self.deepseek_analysis_effort and self.deepseek_analysis_effort not in _valid_efforts:
            logger.warning(
                "[settings] deepseek_analysis_effort=%r 不在允许集合 %s,将被原样发给 API"
                "(非思考模型会忽略,思考模型可能报错)。",
                self.deepseek_analysis_effort,
                ", ".join(sorted(_valid_efforts)),
            )
        elif self.deepseek_analysis_effort in {"low", "medium"}:
            logger.info(
                "[settings] deepseek_analysis_effort=%r 会被 API 映射为 high(无降本效果);"
                "若想显著降低思考 token 成本,请关闭思考模式(deepseek_thinking_enabled=false)。",
                self.deepseek_analysis_effort,
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
