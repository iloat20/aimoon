# SELF_CHECK 阶段 (占位)

纯 LLM 自检,输出 5 项 JSON:
citations_ok / tables_ok / trigger_ok / advice_ok / norepeat_ok / fixes_needed:[]。

任一 false → 把 fixes_needed 喂回 ANALYSIS 重跑该子段(最多 1 次循环)。
