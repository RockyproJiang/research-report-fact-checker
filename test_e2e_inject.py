from __future__ import annotations

import copy
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import config
import ind_baseline
import ind_claims
import step3a_scope as scope
import step3b_compare as cmp
import main_indicators as mi

FAIL = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("OK  " if cond else "FAIL") + f" {name}" + (f"｜{detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def main() -> None:
    # 1) 真实基准 + 真实抽取
    base, sources, evid = mi.load_baselines(lambda s: None)
    known = ind_baseline.known_company_names(base)
    report = config.FILES_DIR / "贵州茅台25年报+26Q1研报.pdf"
    comp, _pages, claims = ind_claims.parse_draft(report, known)
    check("公司识别", comp == "贵州茅台", str(comp))
    comp_base = {p: inds for (c, p), inds in base.items() if c == comp}
    claims = scope.scope_claims(claims)
    for i, c in enumerate(claims, 1):
        c["序号"] = i

    by_ind = {}
    for c in claims:
        by_ind.setdefault((c["指标"], c["报告期"]), c)

    # 2) 注入①：2025A 毛利率改为 0.955
    c = copy.deepcopy(by_ind[("毛利率", "2025年年报")])
    c["数值"] = 0.955
    r = cmp.compare_all([c], comp_base)[0]
    check("注入-错误值→数值不一致", r["判定"] == "疑似错误" and r["差异类型"] == "数值不一致",
          f"{r['判定']}/{r['差异类型']} 差异率={r['差异率']}")

    # 3) 注入②：把数值放到『无基准报告期』(2023)，且值命中 2024 基准 → 年份不一致
    c = copy.deepcopy(by_ind[("流动比率", "2024年年报")])
    c["报告期"] = "2023年年报"
    c["年份"] = 2023
    c["数值"] = comp_base["2024年年报"]["流动比率"]
    r = cmp.compare_all([c], comp_base)[0]
    check("注入-年份标错→年份不一致", r["判定"] == "疑似错误" and r["差异类型"] == "年份不一致",
          f"{r['判定']}/{r['差异类型']}｜{r.get('修改建议', '')[:50]}")

    # 4) 注入③：2026E 预测行 → 跳过
    c = by_ind[("毛利率", "2026年年报")]
    check("注入-预测行→跳过", c["类别"] == "跳过", f"{c['类别']}｜{c['跳过原因']}")

    # 5) 注入④：2024A 流动比率 4.5 → 舍入复核 → 误报剔除
    c = copy.deepcopy(by_ind[("流动比率", "2024年年报")])
    r = cmp.compare_all([c], comp_base)[0]
    check("注入-舍入边界→先疑似", r["判定"] == "疑似错误", f"{r['判定']} 差异率={r['差异率']}")
    cmp.apply_rounding_review([r])
    check("注入-舍入复核→误报剔除", r["判定"] == "误报剔除", f"{r['判定']}｜{r['修改建议'][:40]}")

    # 6) LLM 行映射：误报剔除/疑似错误都应生成复核行
    rows = mi.llm_fix_rows([r])
    check("LLM行映射-误报剔除→复核行", len(rows) == 1 and rows[0]["核查结论"] == "提示",
          str(rows[0]["核查结论"]) if rows else "无")

    print("\n" + ("全部通过 ✔" if not FAIL else f"失败项：{FAIL}"))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
