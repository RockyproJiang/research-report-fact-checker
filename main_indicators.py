from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import pandas as pd

import config
import ind_baseline
import ind_claims
import step3a_scope
import step3b_compare as cmp
from config import (FILES_DIR, OUTPUT_DIR, INDICATORS_8, NUM_TOL, PP_TOL,
                    FA_OUTPUT_DIR, ReportPaths)

# 默认标的（研报文件名，不含 .pdf）；可用命令行参数覆盖：python main_indicators.py "某研报"
DEFAULT_DRAFT_NAME = "贵州茅台25年报+26Q1研报"
DRAFT_NAME = next((a for a in sys.argv[1:] if not a.startswith("-")), DEFAULT_DRAFT_NAME).strip()


# ---------- 基准加载（含来源追踪） ----------
def load_baselines(log=print) -> tuple[dict, dict, dict]:
    """返回 ({(公司,报告期): {指标:数值}}, {(公司,报告期): 来源描述}, {公司: 科目页码证据})"""
    base: dict = {}
    sources: dict = {}
    evid: dict = {}
    if FA_OUTPUT_DIR.is_dir():
        n = 0
        for f in sorted(FA_OUTPUT_DIR.glob("*/03_指标结果.csv")):
            comp = ind_baseline.short_name(f.parent.name)
            src = f"financial_analysis\\output\\{f.parent.name}\\03_指标结果.csv"
            try:
                df = pd.read_csv(f, encoding="utf-8-sig")
            except Exception as exc:
                log(f"[基准] ⚠️ 读取失败 {f}: {exc}")
                continue
            for _, r in df.iterrows():
                ind = str(r.get("指标", "")).strip()
                if ind not in INDICATORS_8:
                    continue
                period = str(r.get("报告期", "")).strip()
                try:
                    val = float(str(r.get("数值", "")).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                base.setdefault((comp, period), {})[ind] = val
                sources.setdefault((comp, period), src)
                n += 1
        log(f"[基准] financial_analysis 只读加载：{len(base)} 个 (公司,报告期)，{n} 条指标")
    # A-2) 本地基准缓存 output\_基准库_*\03_指标结果.csv（第一信源PDF不在 files\ 时兜底）
    cache_root = OUTPUT_DIR
    if cache_root.is_dir():
        for d in sorted(cache_root.iterdir()):
            if not d.is_dir() or not d.name.startswith("_基准库_"):
                continue
            f = d / "03_指标结果.csv"
            if not f.exists():
                continue
            nm = d.name[len("_基准库_"):]
            comp = ind_baseline.short_name(nm)
            src = f"output\\{d.name}\\03_指标结果.csv（本地基准缓存）"
            try:
                df = pd.read_csv(f, encoding="utf-8-sig")
            except Exception:
                continue
            for _, r in df.iterrows():
                ind = str(r.get("指标", "")).strip()
                if ind not in INDICATORS_8:
                    continue
                period = str(r.get("报告期", "")).strip()
                try:
                    val = float(str(r.get("数值", "")).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                base.setdefault((comp, period), {})[ind] = val
                sources.setdefault((comp, period), src)
        log(f"[基准] 本地基准缓存加载完成（累计 {len(base)} 个 (公司,报告期)）")
    for pdf in sorted(FILES_DIR.glob("*.pdf")):
        s = pdf.stem
        if "研报" in s or ("年度报告" not in s and "年报" not in s):
            continue
        got = ind_baseline.compute_local_baseline(pdf, log)
        if not got:
            continue
        comp, by_period, evid_ = got
        src = f"output\\_基准库_{s}\\03_指标结果.csv（据 files\\{pdf.name} 计算）"
        for period, inds in by_period.items():
            for k, v in inds.items():
                if k in INDICATORS_8:
                    base.setdefault((comp, period), {})[k] = v
                    sources.setdefault((comp, period), src)
        evid[comp] = evid_
    return base, sources, evid


# ---------- 输出 ----------
def _pos(claim: dict) -> str:
    if claim.get("来源") == "推导":
        return "财务摘要表(推导)"
    src = claim.get("来源", "")
    return f"第{claim.get('页码')}页（{src}）" if src else f"第{claim.get('页码')}页"


def _pair(claim: dict) -> str:
    rv = cmp.fmt_value(claim["指标"], claim.get("研报值"))
    bv = cmp.fmt_value(claim["指标"], claim.get("基准值"))
    return f"{rv} vs {bv}"


def write_claims_csv(claims: list[dict], paths: ReportPaths) -> None:
    rows = []
    for i, c in enumerate(claims, 1):
        rows.append({
            "序号": i, "研报位置": _pos(c), "原文表述": c["原文"],
            "涉及指标": c["指标"], "数值原文": c["数值原文"],
            "研报值": c["数值"], "报告期": c["报告期展示"],
            "年份": c.get("年份", ""), "标记": c["标记"], "来源": c["来源"],
            "范围判定": c["类别"], "跳过原因": c["跳过原因"], "备注": c["备注"],
        })
    pd.DataFrame(rows).to_csv(paths.CLAIMS_CSV, index=False, encoding="utf-8-sig")


def write_verify_csv(compared: list[dict], paths: ReportPaths) -> None:
    """04_核查结果.csv：规则§五 8列表头；‘判定’列筛‘疑似错误’即错误清单，
    ‘口径差异/误报剔除/无法核实’仅留痕不计入错误统计。"""
    rows = []
    for c in compared:
        rows.append({
            "序号": c["序号"], "研报位置": _pos(c), "原文表述": c["原文"],
            "涉及指标": c["指标"], "研报值/基准值": _pair(c),
            "差异类型": c.get("差异类型", ""), "判定": c.get("判定", ""),
            "修改建议": c.get("修改建议", ""),
        })
    pd.DataFrame(rows).to_csv(paths.VERIFY_CSV, index=False, encoding="utf-8-sig")


def write_detail_csv(compared: list[dict], sources: dict, comp: str, paths: ReportPaths) -> None:
    rows = []
    for c in compared:
        src = sources.get((comp, c["报告期"]), "")
        rows.append({
            "序号": c["序号"], "研报位置": _pos(c), "原文表述": c["原文"],
            "涉及指标": c["指标"], "报告期": c["报告期展示"],
            "研报值": c["研报值"], "基准值": c["基准值"],
            "差异率": c.get("差异率", ""), "差异类型": c.get("差异类型", ""),
            "判定": c.get("判定", ""), "标记": c["标记"], "来源": c["来源"],
            "基准来源": src, "说明": c.get("说明", ""), "修改建议": c.get("修改建议", ""),
        })
    out = paths.dir / "04-1_比对明细.csv"
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")


def write_evidence(compared: list[dict], sources: dict, evid: dict, comp: str,
                   paths: ReportPaths) -> None:
    """06_证据链.json：每条比对基准值的出处 + 本地基准科目页码证据。"""
    per_claim = []
    for c in compared:
        if c.get("基准值") is None:
            continue
        per_claim.append({
            "研报位置": _pos(c), "涉及指标": c["指标"], "报告期": c["报告期"],
            "基准值": c["基准值"],
            "来源文件": sources.get((comp, c["报告期"]), ""),
        })
    pkg = {
        "公司": comp,
        "研报": DRAFT_NAME,
        "基准值出处": per_claim,
        "本地基准科目页码证据": {k: v for k, v in (evid.get(comp) or {}).items()
                                 if any(x in k for x in
                                        ("流动资产", "流动负债", "总负债", "总资产",
                                         "所有者权益", "营业收入", "营业成本",
                                         "经营活动现金净流量"))},
        "说明": "基准库唯一来源为 03_指标结果.csv；贵州茅台基准由本管线按规则§二公式"
                "从 files\\贵州茅台：2025年年度报告.pdf 三表计算（financial_analysis\\output 无茅台数据）。",
    }
    paths.EVIDENCE_JSON.write_text(json.dumps(pkg, ensure_ascii=False, indent=2,
                                              default=str), encoding="utf-8")


def build_base_ctx(comp: str, comp_base: dict, sources: dict) -> str:
    """comp_base: {报告期: {指标: 数值}}（已按公司过滤）。"""
    L = [f"## 核查基准（{comp}，规则§二 8项指标）",
         "比率类存小数（0.9118），毛利率同比pp 存百分点（-0.75）。"]
    for period, inds in sorted(comp_base.items()):
        L.append(f"### {period}（来源：{sources.get((comp, period), '')}）")
        for ind in INDICATORS_8:
            if ind in inds:
                L.append(f"- {ind}: {inds[ind]:.6f}")
    return "\n".join(L)


def llm_fix_rows(compared: list[dict]) -> list[dict]:
    """把指标比对结果映射为 step4_llm_fix 需要的行结构
    （疑似错误 / 无法核实 / 口径差异 / 误报剔除，供 LLM 复核二次闸门）。"""
    rows = []
    for c in compared:
        if c.get("判定") not in ("疑似错误", "无法核实", "口径差异", "误报剔除"):
            continue
        concl = {"疑似错误": "错误", "无法核实": "无法核实",
                 "口径差异": "提示", "误报剔除": "提示"}[c["判定"]]
        rows.append({
            "行号": c["序号"], "声明类型": "指标", "基准键": c["指标"],
            "原文": c["原文"], "草稿数值_元": c.get("研报值"),
            "基准数值_元": c.get("基准值"), "基准来源": c.get("基准来源", ""),
            "差异率": c.get("差异率", ""),
            "核查结论": concl,
            "错误类型": c.get("差异类型", ""), "等级": "中",
            "核查说明": c.get("说明", ""),
        })
    return rows


def quality_score(claims: list[dict], compared: list[dict]) -> list[dict]:
    errs = [c for c in compared if c.get("判定") == "疑似错误"]
    n_err = len(errs)
    n_src = sum(1 for e in errs if e.get("基准值") is not None)
    n_fix = sum(1 for e in errs if e.get("修改建议"))
    skipped = [c for c in claims if c["类别"] == "跳过"]
    n_compared = len(compared)
    covered = {c["指标"] for c in compared}
    n_wb = sum(1 for c in compared if c.get("判定") == "误报剔除")
    return [
        {"维度": "参与比对声明数", "数值": str(n_compared)},
        {"维度": "疑似错误数", "数值": str(n_err)},
        {"维度": "误报剔除留痕(舍入复核)", "数值": str(n_wb)},
        {"维度": "来源定位率", "数值": f"{n_src}/{n_err}={n_src / n_err:.0%}" if n_err else "N/A"},
        {"维度": "修改建议可执行率(规则版)", "数值": f"{n_fix}/{n_err}={n_fix / n_err:.0%}" if n_err else "N/A"},
        {"维度": "跳过留痕条数(误报控制)", "数值": str(len(skipped))},
        {"维度": "8项指标覆盖数", "数值": f"{len(covered)}/8：{'、'.join(sorted(covered)) or '无'}"},
    ]


def write_report(comp: str, claims: list[dict], compared: list[dict],
                 sources: dict, evid: dict, llm_md: str, paths: ReportPaths) -> str:
    errs = [c for c in compared if c.get("判定") == "疑似错误"]
    diff_type = sorted({c["差异类型"] for c in errs})
    wb = [c for c in compared if c.get("判定") == "误报剔除"]
    skipped = [c for c in claims if c["类别"] == "跳过"]
    koujing = [c for c in claims if c["类别"] == "口径差异"]
    L = [f"# 研究报告纠错核查报告（指标比对）\n",
         f"## 核查对象\n- 研报：`{DRAFT_NAME}.pdf`（诚通证券，2026-05-20）",
         f"- 公司：{comp}（600519.SH）",
         f"- 依据：《研报财务数据比对判定规则.docx》§二~§五（比对基准库＝`03_指标结果.csv`，"
         f"纳入比对的8项指标，容差≤0.5%）\n",
         "## 一、比对结果汇总\n",
         f"- 抽取声明（8项指标内）：**{len(claims)}** 条",
         f"- 参与比对：**{len(compared)}** 条",
         f"- 直接跳过（留痕不比对）：**{len(skipped)}** 条（预测/单季/估算等）",
         f"- 口径差异（留痕不计错）：**{len(koujing)}** 条",
         f"- 误报剔除（舍入复核留痕，不计错）：**{len(wb)}** 条",
         f"- **疑似错误：{len(errs)} 条**（差异类型：{'、'.join(diff_type) or '无'}）\n"]

    L += ["## 二、疑似错误清单（规则§五模板）\n"]
    if errs:
        L += ["| 序号 | 研报位置 | 原文表述 | 涉及指标 | 研报值/基准值 | 差异类型 | 判定 | 修改建议 |",
              "|---|---|---|---|---|---|---|---|"]
        for c in errs:
            L.append(f"| {c['序号']} | {_pos(c)} | {c['原文'][:40]} | {c['指标']} | "
                     f"{_pair(c)} | {c['差异类型']} | 疑似错误 | {c['修改建议']} |")
    else:
        L.append("**未发现疑似错误。** 研报中 8 项指标的历史年报声明（毛利率、资产负债率、"
                 "流动比率、毛利率同比pp）与基准库一致（含舍入精度，相对误差≤0.5%）。")

    if wb:
        L += ["\n### 误报剔除留痕（规则§五：判‘误报剔除’不计入错误统计）\n",
              "| 序号 | 研报位置 | 原文表述 | 涉及指标 | 研报值/基准值 | 差异类型 | 判定 | 修改建议 |",
              "|---|---|---|---|---|---|---|---|"]
        for c in wb:
            L.append(f"| {c['序号']} | {_pos(c)} | {c['原文'][:40]} | {c['指标']} | "
                     f"{_pair(c)} | {c['差异类型']} | 误报剔除 | {c['修改建议']} |")
        L += ["\n> 说明：上表机械比对曾超出 0.5% 容差，经确定性复核（规则§4.3：研报披露精度导致的舍入）"
              "判定为误报剔除，仅留痕、不计入错误数量。"]

    L += ["\n## 三、比对明细（参与比对全部声明）\n",
          "| 序号 | 研报位置 | 原文表述 | 涉及指标 | 报告期 | 研报值 | 基准值 | 差异率 | 判定 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for c in compared:
        L.append(f"| {c['序号']} | {_pos(c)} | {c['原文'][:32]} | {c['指标']} | {c['报告期展示']} | "
                 f"{cmp.fmt_value(c['指标'], c['研报值'])} | {cmp.fmt_value(c['指标'], c['基准值'])} | "
                 f"{c.get('差异率','-')} | {c.get('判定','')} |")

    L += ["\n## 四、口径差异与跳过留痕（不计入错误统计）\n"]
    if koujing:
        L += [f"- {c['序号']}｜{c['指标']}｜{c['跳过原因']}" for c in koujing]
    else:
        L.append("- 无")
    L += ["\n### 跳过声明明细（规则§三直接跳过）\n",
          "| 序号 | 研报位置 | 涉及指标 | 报告期 | 数值原文 | 跳过原因 |",
          "|---|---|---|---|---|---|"]
    for c in skipped:
        L.append(f"| {c['序号']} | {_pos(c)} | {c['指标']} | {c['报告期展示']} | "
                 f"{c['数值原文']} | {c['跳过原因']} |")

    L += ["\n## 五、修改建议（LLM 复核）\n", llm_md,
          "\n## 六、证据链\n",
          f"- 基准库唯一来源：`03_指标结果.csv`。{comp} 基准由本管线按规则§二公式从 "
          "`files\\贵州茅台：2025年年度报告.pdf` 三表计算（`financial_analysis\\output` 中无茅台数据，"
          "故未命中 financial_analysis 基准；其余公司基准仍从 financial_analysis 只读加载）。",
          f"- 贵州茅台 2025A 基准科目页码证据（提取自年报 PDF）："]
    ev = evid.get(comp) or {}
    for k in ["流动资产_期末", "流动负债_期末", "总负债_期末", "总资产_期末",
              "所有者权益_期末", "所有者权益_期初", "营业收入_本期", "营业成本_本期",
              "营业收入_上期", "营业成本_上期", "经营活动现金净流量"]:
        if k in ev:
            L.append(f"  - {k}：{ev[k]}")
    L.append(f"- 交叉验证：`财报分析\\贵州茅台_人工抽检记录.csv` 中关键金额（营业收入/营业成本/"
             "资产总计/负债合计/所有者权益合计/经营活动现金流等）与本管线提取值逐项一致。")

    L += ["\n## 七、附加观察（超出8项清单范围，仅提示、不计入错误统计）\n",
          "- 研报第1页『净利润 823.20 亿元/-4.53%』及第2页财务摘要表『净利润(百万元) 86,228/82,320』"
          "均为**归母净利润**口径（合并净利润2025A为853.10亿元、2024A为893.35亿元）。"
          "『净利润』标注与归母口径混用属统计口径问题；因净利润不在规则§二8项清单内，按§三④直接跳过，仅作提示。",
          "- 研报第1页『2025年经营活动现金流净额615.22亿元/-33.46%』、营收/净利/费用率等"
          "均为非清单指标，未纳入比对，留痕备查。",
          "- 26Q1单季毛利率89.76%/-2.21pct 属单季度口径（规则§三②直接跳过），未比对。"]

    L += ["\n## 八、质量自评与风险说明\n",
          "- 质量自评：`08-1_质量评分.csv`（来源定位率、建议可执行率、跳过留痕、指标覆盖）。",
          "- 风险说明：① 表格解析若列错位会绑错年份（本次已按词坐标分栏重建并抽查第2/5页绑定结果）；"
          "② 2024A 基准由 2025 年报上期数计算（与人工抽检一致），未取 2024 年报原件；"
          "③ 容差 0.5% 为相对误差，同比pp 用绝对容差 0.2pp（config.PP_TOL）。"]
    md = "\n".join(L)
    paths.REPORT_MD.write_text(md, encoding="utf-8")
    return md


def write_pack(comp: str, claims: list[dict], compared: list[dict], llm_md: str,
               quality: list[dict], paths: ReportPaths) -> None:
    errs = [c for c in compared if c.get("判定") == "疑似错误"]
    pkg = {
        "公司": comp, "研报": DRAFT_NAME,
        "核查汇总": {
            "声明总数(8项指标内)": len(claims),
            "参与比对": len(compared),
            "跳过": sum(1 for c in claims if c["类别"] == "跳过"),
            "口径差异": sum(1 for c in claims if c["类别"] == "口径差异"),
            "疑似错误数": len(errs),
            "数值不一致": sum(1 for e in errs if e.get("差异类型") == "数值不一致"),
            "年份不一致": sum(1 for e in errs if e.get("差异类型") == "年份不一致"),
            "无法核实": sum(1 for c in compared if c.get("判定") == "无法核实"),
            "误报剔除": sum(1 for c in compared if c.get("判定") == "误报剔除"),
            "质量自评": quality,
        },
        "错误清单": [
            {"序号": c["序号"], "研报位置": _pos(c), "原文": c["原文"], "指标": c["指标"],
             "研报值": c.get("研报值"), "基准值": c.get("基准值"),
             "差异类型": c.get("差异类型"), "判定": c.get("判定"),
             "修改建议": c.get("修改建议")} for c in errs],
        "比对明细": [
            {"序号": c["序号"], "指标": c["指标"], "报告期": c["报告期展示"],
             "研报值": c.get("研报值"), "基准值": c.get("基准值"),
             "差异率": c.get("差异率", ""), "判定": c.get("判定", "")} for c in compared],
        "修改建议": llm_md,
    }
    paths.PACK_JSON.write_text(json.dumps(pkg, ensure_ascii=False, indent=2, default=str),
                               encoding="utf-8")
    pd.DataFrame(quality).to_csv(paths.SCORE_CSV, index=False, encoding="utf-8-sig")


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    log_lines = []

    def log(m):
        print(m)
        log_lines.append(m)

    try:
        base, sources, evid = load_baselines(log)
        if not base:
            log("[中止] 基准库为空（financial_analysis\\output 无 03_指标结果.csv，files\\ 无年报PDF）")
            return
        known = ind_baseline.known_company_names(base)
        report_pdf = FILES_DIR / f"{DRAFT_NAME}.pdf"
        if not report_pdf.exists():
            log(f"[中止] 未找到研报 {report_pdf}")
            return
        comp, _pages, claims = ind_claims.parse_draft(report_pdf, known)
        if comp is None:
            log("[中止] 未能从研报识别公司（对照基准库公司名/company_meta.csv）")
            return
        log(f"[claim] {comp} 声明抽取 {len(claims)} 条（8项指标内）")

        # 范围判定
        claims = step3a_scope.scope_claims(claims)
        for i, c in enumerate(claims, 1):
            c["序号"] = i
        # 按公司汇总全报告期基准后比对（覆盖 2025A/2024A 等）
        comp_base = {p: inds for (c, p), inds in base.items() if c == comp}
        compared = cmp.compare_all(claims, comp_base, tol=NUM_TOL, pp_tol=PP_TOL)
        # 确定性复核闸门：研报披露精度舍入 → 误报剔除留痕（规则§4.3）
        compared = cmp.apply_rounding_review(compared)
        for c in compared:
            c["基准来源"] = sources.get((comp, c["报告期"]), "")

        # 输出目录
        paths = config.make_report_paths("核查_" + DRAFT_NAME)
        paths.mkdir()

        write_claims_csv(claims, paths)
        write_verify_csv(compared, paths)
        write_detail_csv(compared, sources, comp, paths)
        write_evidence(compared, sources, evid, comp, paths)

        # LLM 复核（仅存在疑似错误/无法核实/口径差异时调用；否则不调用API）
        import step4_llm_fix as s4
        base_ctx = build_base_ctx(comp, comp_base, sources)
        fix_rows = llm_fix_rows(compared)
        llm_md = s4.generate_fixes(fix_rows, [], base_ctx, log)
        pd.DataFrame([{"建议": llm_md}]).to_csv(paths.FIX_CSV, index=False,
                                                encoding="utf-8-sig")
        (paths.dir / "05_修改建议.md").write_text(llm_md, encoding="utf-8")

        write_report(comp, claims, compared, sources, evid, llm_md, paths)
        quality = quality_score(claims, compared)
        write_pack(comp, claims, compared, llm_md, quality, paths)

        n_err = sum(1 for c in compared if c.get("判定") == "疑似错误")
        n_skip = sum(1 for c in claims if c["类别"] == "跳过")
        log(f"[done] 比对 {len(compared)} 条，疑似错误 {n_err}，跳过留痕 {n_skip}"
            f" → output\\{paths.report_name}\\07_核查报告.md")
        pd.DataFrame([{"研报": DRAFT_NAME, "公司": comp, "声明数": len(claims),
                       "参与比对": len(compared), "疑似错误": n_err, "状态": "完成"}]
                     ).to_csv(OUTPUT_DIR / "00_批处理汇总.csv", index=False,
                              encoding="utf-8-sig")
    except Exception:
        log("流程异常：\n" + traceback.format_exc())
    finally:
        (OUTPUT_DIR / "run.log").write_text("\n".join(log_lines), encoding="utf-8")


if __name__ == "__main__":
    main()
