"""Unified configuration via Pydantic Settings, loaded from .env / env vars."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
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
# 官方弃用截止日(北京时间 2026/07/24 23:59)。过此日后旧模型名 API 直接 400,
# 告警文案从「将于」升级为「已于…弃用,现在会 400」,让日志一眼看出是硬失效而非提醒。
_DEEPSEEK_DEPRECATION_CUTOFF = date(2026, 7, 24)


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
    # 默认 high(DIRECT 直出一次成文,无需最深推理;max 的思考 token 成本显著更高
    # 而对结构化直出质量增益有限)。确需最深推理可显式设 DEEPSEEK_ANALYSIS_EFFORT=max。
    deepseek_analysis_effort: str = "high"
    # ANALYSIS 骨架 JSON 的输出上限。骨架比旧初稿短得多,4096 已留充足余量。
    deepseek_analysis_max_tokens: int = 4096

    # 思考模式开关(官方参数 thinking:{type:enabled/disabled},默认 enabled)。
    # None(默认)         = 不显式覆盖,沿用模型默认(对 v4-* 即 enabled);
    # True                = 强制开启思考 + 发送 reasoning_effort(质量最高,思考 token 按输出计费);
    # False               = 关闭思考(纯扩写/格式化场景),不再发 reasoning_effort,
    #                       改回传 temperature(top_p 等仍忽略)。
    # 注: 旧字段 deepseek_reasoner_enabled 已移除(2026-07-19 架构审查);
    # 其兼容别名职责由 deepseek_thinking_enabled 单独承担。
    deepseek_thinking_enabled: bool | None = None

    # ---- LongCat 提供商(OpenAI 兼容 / Anthropic 兼容 API) ----
    # 与 DeepSeek 二选一,由 ai_provider 切换(默认 deepseek,保持现有行为不变)。
    # 官方端点 https://api.longcat.chat,OpenAI 格式 /openai/v1/chat/completions。
    # 当前模型 LongCat-2.0;思考参数用 thinking:{type:enabled/disabled}(无 reasoning_effort)。
    ai_provider: str = "deepseek"
    longcat_api_key: str = ""
    longcat_base_url: str = "https://api.longcat.chat"
    longcat_model: str = "LongCat-2.0"
    # 直出/扩写输出上限(模型总上限 131072,此值仅安全天花板)。
    longcat_max_tokens: int = 24576
    # ANALYSIS 骨架 JSON 输出上限
    longcat_analysis_max_tokens: int = 4096
    longcat_temperature: float = 0.3
    # 思考开关:None = 沿用模型默认(enabled);True/False 强制开/关。
    longcat_thinking_enabled: bool | None = None

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

    # 终端输出开关: 是否在终端实时打印 AI 报告正文(## 章节横幅 + 正文行)。
    # 默认 False = 报告只落 HTML 文件,不往终端刷正文(减少噪音);
    # 设 True(环境变量 STREAM_REPORT_TO_TERMINAL=true)可恢复流式打印,便于实时观察进度。
    stream_report_to_terminal: bool = False

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

    @property
    def deepseek(self) -> DeepSeekConfig:
        """DeepSeek 提供商分组配置视图(只读,由扁平 deepseek_* 字段聚合)。"""
        return DeepSeekConfig(
            api_key=self.deepseek_api_key,
            base_url=self.deepseek_base_url,
            model=self.deepseek_model,
            max_tokens=self.deepseek_max_tokens,
            analysis_max_tokens=self.deepseek_analysis_max_tokens,
            temperature=self.deepseek_temperature,
            thinking_enabled=self.deepseek_thinking_enabled,
            analysis_effort=self.deepseek_analysis_effort,
        )

    @property
    def longcat(self) -> LongCatConfig:
        """LongCat 提供商分组配置视图(只读,由扁平 longcat_* 字段聚合)。"""
        return LongCatConfig(
            api_key=self.longcat_api_key,
            base_url=self.longcat_base_url,
            model=self.longcat_model,
            max_tokens=self.longcat_max_tokens,
            analysis_max_tokens=self.longcat_analysis_max_tokens,
            temperature=self.longcat_temperature,
            thinking_enabled=self.longcat_thinking_enabled,
        )

    @model_validator(mode="after")
    def _warn_unknown_model(self) -> Settings:
        """启动期可见性: 模型名非已知公开模型 / 已弃用 / effort 非法时告警(不阻断运行)。

        仅在 ai_provider=deepseek 时校验 DeepSeek 专属模型名 / effort;切换到
        LongCat 等其它提供商时跳过这些 DeepSeek 专属告警。
        """
        if self.ai_provider != "deepseek":
            return self
        if self.deepseek_model and self.deepseek_model not in _KNOWN_DEEPSEEK_MODELS:
            logger.warning(
                "[settings] deepseek_model=%r 不是已知 DeepSeek 公开模型(%s)。"
                "若 API 返回 400 将触发整条重试并静默降级,既浪费 token 又拉低报告质量;"
                "请通过 DEEPSEEK_MODEL 环境变量设置为正确模型。",
                self.deepseek_model,
                ", ".join(sorted(_KNOWN_DEEPSEEK_MODELS)),
            )
        if self.deepseek_model in _DEPRECATED_DEEPSEEK_MODELS:
            if date.today() > _DEEPSEEK_DEPRECATION_CUTOFF:
                logger.warning(
                    "[settings] deepseek_model=%r 已于 2026/07/24 弃用,官方 API 现在"
                    "会直接返回 400 并触发整条重试 + 静默降级;请立即迁移到 "
                    "deepseek-v4-flash(思考模式,等价原 reasoner)或 deepseek-v4-pro。",
                    self.deepseek_model,
                )
            else:
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


@dataclass
class DeepSeekConfig:
    """DeepSeek 提供商的分组配置视图。

    存储仍为 ``Settings`` 上的扁平 ``deepseek_*`` 字段(保持 .env 的
    ``DEEPSEEK_API_KEY`` 扁平命名与测试 kwargs 兼容);本类仅提供按提供商聚类的
    只读视图,供 ``resolve_ai_provider`` 等消费者以 ``settings.deepseek`` 干净访问,
    避免散落的 ``deepseek_*`` 字段重复。
    """

    api_key: str
    base_url: str
    model: str
    max_tokens: int
    analysis_max_tokens: int
    temperature: float
    thinking_enabled: bool | None
    analysis_effort: str


@dataclass
class LongCatConfig:
    """LongCat 提供商的分组配置视图(语义同 ``DeepSeekConfig``)。"""

    api_key: str
    base_url: str
    model: str
    max_tokens: int
    analysis_max_tokens: int
    temperature: float
    thinking_enabled: bool | None


@dataclass
class AIProviderConfig:
    """Resolved, provider-agnostic AI configuration.

    Produced by :func:`resolve_ai_provider` so the transport/analyzer layers
    never need to branch on ``ai_provider`` themselves — they just read these
    fields.
    """

    provider: str
    api_key: str
    base_url: str
    chat_path: str
    model: str
    max_tokens: int
    analysis_max_tokens: int
    temperature: float
    # None => provider default (deepseek/longcat both default thinking ON).
    thinking_enabled: bool | None
    # DeepSeek 支持 reasoning_effort;LongCat 不支持(思考只用 thinking:{type})。
    supports_reasoning_effort: bool
    # DeepSeek 专属思考强度(high/max);LongCat 无 -> None。
    analysis_effort: str | None

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.chat_path}"


def _deepseek_cfg(settings: object) -> DeepSeekConfig:
    """提取 DeepSeek 配置:优先用 Settings.deepseek 分组视图,回退到扁平字段(兼容测试 fake)。"""
    view = getattr(settings, "deepseek", None)
    if isinstance(view, DeepSeekConfig):
        return view
    return DeepSeekConfig(
        api_key=getattr(settings, "deepseek_api_key", "") or "",
        base_url=getattr(settings, "deepseek_base_url", "") or "https://api.deepseek.com",
        model=getattr(settings, "deepseek_model", "") or "deepseek-v4-flash",
        max_tokens=int(getattr(settings, "deepseek_max_tokens", 24576) or 24576),
        analysis_max_tokens=int(getattr(settings, "deepseek_analysis_max_tokens", 4096) or 4096),
        temperature=float(getattr(settings, "deepseek_temperature", 0.3) or 0.3),
        thinking_enabled=getattr(settings, "deepseek_thinking_enabled", None),
        analysis_effort=getattr(settings, "deepseek_analysis_effort", None) or "high",
    )


def _longcat_cfg(settings: object) -> LongCatConfig:
    """提取 LongCat 配置:语义同 ``_deepseek_cfg``。"""
    view = getattr(settings, "longcat", None)
    if isinstance(view, LongCatConfig):
        return view
    return LongCatConfig(
        api_key=getattr(settings, "longcat_api_key", "") or "",
        base_url=getattr(settings, "longcat_base_url", "") or "https://api.longcat.chat",
        model=getattr(settings, "longcat_model", "") or "LongCat-2.0",
        max_tokens=int(getattr(settings, "longcat_max_tokens", 24576) or 24576),
        analysis_max_tokens=int(getattr(settings, "longcat_analysis_max_tokens", 4096) or 4096),
        temperature=float(getattr(settings, "longcat_temperature", 0.3) or 0.3),
        thinking_enabled=getattr(settings, "longcat_thinking_enabled", None),
    )


def resolve_ai_provider(settings: object) -> AIProviderConfig:
    """Return the active provider config based on ``settings.ai_provider``.

    经 ``Settings.deepseek`` / ``Settings.longcat`` 分组视图(或扁平字段回退)
    提取配置,消除 deepseek/longcat 分支中重复的字段读取;provider 专属告警
    仍由 ``Settings._warn_unknown_model`` 在加载期负责。
    """
    provider = (getattr(settings, "ai_provider", "deepseek") or "deepseek").lower()
    if provider == "longcat":
        lc = _longcat_cfg(settings)
        return AIProviderConfig(
            provider="longcat",
            api_key=lc.api_key,
            base_url=lc.base_url,
            chat_path="/openai/v1/chat/completions",
            model=lc.model,
            max_tokens=lc.max_tokens,
            analysis_max_tokens=lc.analysis_max_tokens,
            temperature=lc.temperature,
            thinking_enabled=lc.thinking_enabled,
            supports_reasoning_effort=False,
            analysis_effort=None,
        )
    # deepseek (default)
    ds = _deepseek_cfg(settings)
    # 新默认(C2 降本): 未显式配置时,DIRECT 直出默认关闭思考。
    # 思考 token 按输出计价、是最大成本项,而直出报告有 direct.md 强约束,
    # 关思考对质量影响有限。需最深推理请显式 DEEPSEEK_THINKING_ENABLED=true。
    explicit = ds.thinking_enabled if ds.thinking_enabled is not None else False
    # effort 归一(C3): low/medium 会被 API 静默映射为 high 且无降本效果,
    # 直接归一为 high,避免发送无意义档位。
    effort = ds.analysis_effort or "high"
    if effort in {"low", "medium"}:
        effort = "high"
    return AIProviderConfig(
        provider="deepseek",
        api_key=ds.api_key,
        base_url=ds.base_url,
        chat_path="/v1/chat/completions",
        model=ds.model,
        max_tokens=ds.max_tokens,
        analysis_max_tokens=ds.analysis_max_tokens,
        temperature=ds.temperature,
        thinking_enabled=explicit,
        supports_reasoning_effort=True,
        analysis_effort=effort,
    )


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
