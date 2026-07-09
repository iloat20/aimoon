"""社媒情感分析工具(纯函数,零 LLM token)。

对 social_posts 的标题+正文做 正/负/中 三分类,输出比例与高频词,量化市场情绪。

策略:
- 主路径:若已安装 SnowNLP(可选依赖),用其情感打分(>0.5 偏正面,<0.5 偏负面)。
- 兜底:内置金融情感词典,按正/负关键词命中计数分类(无需任何第三方包)。
- 高频词:优先 jieba 分词;无 jieba 时用 CJK 2-gram + 停用词过滤。

所有结果仅统计已抓取文本,不编造;无帖子时返回 partial。
"""
from __future__ import annotations

import logging
import re

from aimoon.adapters.driven.ai.tools._safe import tool_safe
from aimoon.core.domain.entities.social import SocialPost

logger = logging.getLogger(__name__)

# 金融情感词典(面向 A 股社媒语境)
_POS_WORDS = [
    "利好", "上涨", "买入", "看好", "增长", "超预期", "回购", "分红", "低估",
    "机会", "反弹", "突破", "创新高", "需求旺盛", "提价", "景气", "护城河",
    "高分红", "价值", "加仓", "布局", "拐点", "困境反转", "低估", "性价比",
]
_NEG_WORDS = [
    "下跌", "看空", "风险", "利空", "暴雷", "亏损", "下滑", "减持", "破千",
    "破发", "套牢", "泡沫", "高估", "压力", "担忧", "收紧", "限购", "库存积压",
    "跌停", "杀跌", "退市", "商誉减值", "坏账", "回落", "承压", "不及预期",
]

_STOPWORDS = set(
    "的 了 在 是 我 你 他 她 它 们 这 那 有 和 与 及 或 也 都 就 不 没 很 "
    "被 把 让 给 对 从 向 到 于 以 为 之 其 此 等 个 家 家 股 元 亿 万 元 "
    "今天 昨日 昨日 现在 目前 我们 他们 你们 自己 什么 怎么 为什么 可以 "
    "已经 还是 因为 所以 但是 如果 这个 那个 一个 没有 不是 就是 还是 "
    "a an the of to and or in on for is are be with at by from as it its "
    .split()
)


@tool_safe("computation_error")
def run(posts: list[SocialPost] | tuple[SocialPost, ...] | None) -> dict[str, object]:
    if not posts:
        return {"__partial__": "no_posts"}

    texts: list[str] = []
    for p in posts:
        title = getattr(p, "title", "") or ""
        content = getattr(p, "content", "") or ""
        t = f"{title} {content}".strip()
        if t:
            texts.append(t)
    if not texts:
        return {"__partial__": "empty_texts"}

    has_snownlp, sn = _try_snownlp()
    scores: list[float] = []  # 每条 0~1,>0.5 正
    if has_snownlp:
        for t in texts:
            try:
                scores.append(float(sn(t).sentiments))
            except Exception:
                scores.append(0.5)
    else:
        for t in texts:
            scores.append(_lexicon_score(t))

    pos = sum(1 for s in scores if s > 0.55)
    neg = sum(1 for s in scores if s < 0.45)
    neu = len(scores) - pos - neg
    n = len(scores)

    top_kw = _keyword_freq(texts, top=12)
    pos_hits = _count_lexicon(texts, _POS_WORDS)
    neg_hits = _count_lexicon(texts, _NEG_WORDS)

    # 整体情绪指数: 偏正面比例 - 偏负面比例 (-1~1)
    sentiment_index = round((pos - neg) / n, 3) if n else 0.0
    label = "偏正面" if sentiment_index > 0.1 else ("偏负面" if sentiment_index < -0.1 else "中性")

    return {
        "total": n,
        "pos": pos,
        "neg": neg,
        "neu": neu,
        "pos_ratio": round(pos / n, 3),
        "neg_ratio": round(neg / n, 3),
        "neu_ratio": round(neu / n, 3),
        "sentiment_index": sentiment_index,
        "label": label,
        "engine": "snownlp" if has_snownlp else "lexicon",
        "top_keywords": top_kw,
        "pos_words": sorted(pos_hits.items(), key=lambda x: -x[1])[:8],
        "neg_words": sorted(neg_hits.items(), key=lambda x: -x[1])[:8],
    }


def _try_snownlp():
    try:
        from snownlp import SnowNLP

        return True, SnowNLP
    except Exception:
        return False, None


def _lexicon_score(text: str) -> float:
    """基于正/负关键词净命中给出 0~1 情感分(0.5 为中性)。"""
    pos = sum(1 for w in _POS_WORDS if w in text)
    neg = sum(1 for w in _NEG_WORDS if w in text)
    if pos == 0 and neg == 0:
        return 0.5
    # 净命中映射到 (0,1),避免极端
    net = (pos - neg) / (pos + neg)
    return max(0.05, min(0.95, 0.5 + net * 0.45))


def _count_lexicon(texts: list[str], lexicon: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in texts:
        for w in lexicon:
            if w in t:
                counts[w] = counts.get(w, 0) + 1
    return counts


def _keyword_freq(texts: list[str], top: int = 12) -> list[dict[str, object]]:
    """提取高频词(去停用词)。"""
    has_jieba, jieba = _try_jieba()
    counter: dict[str, int] = {}
    for t in texts:
        toks: list[str] = []
        if has_jieba:
            try:
                toks = [w for w in jieba.lcut(t) if len(w) >= 2 and w not in _STOPWORDS]
            except Exception:
                toks = []
        if not toks:
            toks = _cjk_bigrams(t)
        for w in toks:
            counter[w] = counter.get(w, 0) + 1
    ranked = sorted(counter.items(), key=lambda x: -x[1])[:top]
    return [{"word": w, "count": c} for w, c in ranked]


def _try_jieba():
    try:
        import jieba

        return True, jieba
    except Exception:
        return False, None


_CJK_RE = re.compile(r"[一-鿿]")


def _cjk_bigrams(text: str) -> list[str]:
    """无 jieba 时的兜底:提取连续 CJK 2-gram,过滤停用词与纯标点。"""
    chars = [c for c in text if _CJK_RE.match(c)]
    out: list[str] = []
    for i in range(len(chars) - 1):
        bg = chars[i] + chars[i + 1]
        if bg not in _STOPWORDS:
            out.append(bg)
    return out
