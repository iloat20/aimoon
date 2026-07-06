from __future__ import annotations

import re
from dataclasses import dataclass

# 报告八个核心章节的判定关键词(用于覆盖度统计)。
# 匹配规则: 章节编号 "一/二/..." 或典型小节标题关键词,允许前导 ## 空格等。
SECTION_KEYWORDS: list[tuple[str, list[str]]] = [
    ("一、业务画像与护城河", ["业务画像", "护城河", "核心业务结构", "商业模式"]),
    ("二、财务健康诊断", ["财务健康", "成长性", "现金流质量", "ROE 杜邦", "ROE杜邦"]),
    ("三、交叉验证", ["交叉验证", "业务 vs 财务", "业务/财务背离", "四方验证"]),
    ("四、风险量化与看空", ["风险量化", "看空", "触发条件", "冲击量级"]),
    ("五、估值建模", ["估值建模", "FCFE", "保守档", "中性档", "乐观档"]),
    ("六、逆向视角", ["逆向视角", "看多逻辑", "安全边际", "市场错在哪里"]),
    ("七、投资建议", ["投资建议", "买入/增持/持有", "目标价格区间", "止损"]),
    ("八、附录", ["附录", "财务时序表", "同行竞品对比表", "估值三档表"]),
]


def _normalize(text: str) -> str:
    """把空白/全半角折叠,便于宽松的子串/正则匹配。"""
    text = text.replace(" ", "").replace("　", "").replace("\t", "")
    # 全角括号/冒号统一为半角
    text = text.replace("（", "(").replace("）", ")").replace("：", ":")
    return text


@dataclass
class SectionCoverage:
    total: int
    hit: int
    missing: list[str]
    per_section: dict[str, bool]

    @property
    def ratio(self) -> float:
        return self.hit / self.total if self.total else 0.0

    @property
    def all_present(self) -> bool:
        return self.hit == self.total


def count_headings(markdown: str) -> dict[str, int]:
    """统计 ``## 标题`` 出现次数,用于辅助覆盖度判定。"""
    counts: dict[str, int] = {}
    for line in markdown.splitlines():
        m = re.match(r"^#{2,3}\s+(.+)$", line.strip())
        if m:
            title = m.group(1).strip()
            counts[title] = counts.get(title, 0) + 1
    return counts


def evaluate_coverage(markdown: str, *, require: int = 7) -> SectionCoverage:
    """评估八个核心章节的覆盖度。

    判定方式(任一命中即视为该章节存在):
      1. 章节编号(如 "一、"、"## 一、"、"### 一、");
      2. 任意一个章节关键词出现在文档中(空白折叠后子串匹配)。
    """
    norm = _normalize(markdown)
    per_section: dict[str, bool] = {}
    for section_name, keywords in SECTION_KEYWORDS:
        hit = False
        # 直接命中章节名
        if _normalize(section_name) in norm:
            hit = True
        else:
            # 命中任一编号数字或是关键词
            num = section_name.split("、")[0]  # e.g. "一"
            if re.search(rf"(?:^|[^一-鿿]){re.escape(num)}\s*、", markdown):
                hit = True
            else:
                for kw in keywords:
                    if _normalize(kw) in norm:
                        hit = True
                        break
        per_section[section_name] = hit
    missing = [k for k, v in per_section.items() if not v]
    return SectionCoverage(
        total=len(SECTION_KEYWORDS),
        hit=len(SECTION_KEYWORDS) - len(missing),
        missing=missing,
        per_section=per_section,
    )


def coverage_ge(markdown: str, n: int = 7) -> bool:
    """断言至少 ``n`` 个章节存在(默认 7/8)。"""
    return evaluate_coverage(markdown).hit >= n
