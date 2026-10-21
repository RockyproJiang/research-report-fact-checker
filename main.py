import sys
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import re
import json
import shutil
import traceback
from pathlib import Path
import pandas as pd

import config
import step2_standardize as s2
import step3_verify as s3
import step4_llm_fix as s4
import step5_structured as s5
import ind_baseline
import ind_claims
import step3a_scope
import step3b_compare as cmp
from draft_parser import parse_draft
from config import (OUTPUT_DIR, FILES_DIR, FA_OUTPUT_DIR, INDICATORS_8,
                    NUM_TOL, PP_TOL, ReportPaths)

DRAFT_EXTS = (".md", ".txt", ".docx", ".pdf")


def init_output(log=print) -> None:
    """初始化：删除 output/ 目录下全部文件与子目录（保留 output 目录本身）。
    供 `python main.py --init` 调用，用于清空历史核查产物与基准缓存后重跑。"""
    if not OUTPUT_DIR.exists():
        return
    removed = 0
    for p in OUTPUT_DIR.iterdir():
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed += 1
        except Exception as exc:
            log(f"[init] 删除失败 {p.name}: {exc}")
    log(f"[init] 已清空 output/：删除 {removed} 项（含历史核查与基准缓存）")


def short_name(folder: str) -> str:
    return str(folder).split("：")[0].split(":")[0].strip()


def _read_keyinfo(dirp: Path) -> dict:
    f = dirp / "01_关键信息.csv"
    if not f.exists():
        return {}
    try:
        df = pd.read_csv(f, encoding="utf-8-sig")
        return dict(zip(df["字段"], df["值"]))
    except Exception:
        return {}


def _read_std(dirp: Path) -> pd.DataFrame | None:
    f = dirp / "02_标准化数据.csv"
    if not f.exists():
        return None
    try:
        return pd.read_csv(f, encoding="utf-8-sig")
    except Exception:
        return None


def _read_indicators(dirp: Path) -> dict:
    f = dirp / "03_指标结果.csv"
    out: dict = {}
    if not f.exists():
        return out
    try:
        df = pd.read_csv(f, encoding="utf-8-sig")
    except Exception:
        return out
    for _, r in df.iterrows():
        ind = str(r.get("指标", "")).strip()
        if ind not in INDICATORS_8:
            continue
        period = str(r.get("报告期", "")).strip()
        try:
            val = float(str(r.get("数值", "")).replace(",", ""))
        except (TypeError, ValueError):
            continue
        out.setdefault(period, {})[ind] = val
    return out


def load_company_baseline(comp: str, log=print) -> dict | None:
    """在 output 中定位该公司财报分析基准，返回
    {'d', 'key_info', 'indicators', 'base_dir', 'period', 'source'} 或 None。"""
    cands = []

    def scan(root: Path, prefix: str = ""):
        if not root.is_dir():
            return
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            nm = d.name
            if prefix and nm.startswith(prefix):
                nm = nm[len(prefix):]
            if short_name(nm) == comp:
                cands.append(d)

    scan(FA_OUTPUT_DIR)                   # financial_analysis/output/<公司：报告期>
    scan(OUTPUT_DIR, "_基准库_")            # output/_基准库_<公司：报告期>

    if not cands:
        log(f"[基准] output 中未找到 {comp} 的财报分析（financial_analysis/output 与 _基准库 均无）")
        return None
    # 取最新报告期目录（文件名含 20xx）
    def _yr(d):
        m = re.search(r"(20\d{2})", d.name)
        return int(m.group(1)) if m else 0
    base_dir = max(cands, key=_yr)
    std = _read_std(base_dir)
    if std is None or std.empty:
        log(f"[基准] {base_dir.name} 无 02_标准化数据.csv，基准不可用")
        return None
    companies, _cont = s2.assemble(std)
    best = None
    for rep in companies.values():
        for it in rep.values():
            key = (it["year"] or 0, it["period"])
            if best is None or key > best[0]:
                best = (key, it)
    if best is None:
        log(f"[基准] {base_dir.name} 标准化无可用报告期")
        return None
    (_, it) = best
    period = f"{it['year']}年{it['period']}"
    kind = "financial_analysis（只读）" if base_dir.is_relative_to(FA_OUTPUT_DIR) else "本地基准缓存"
    log(f"[基准] {comp} {period} 就绪（{kind}：{base_dir.name}）")
    return {"d": it["d"], "evid": it["evid"], "key_info": _read_keyinfo(base_dir),
            "indicators": _read_indicators(base_dir), "base_dir": base_dir,
            "period": period,
            "source": f"{base_dir.name}（{'financial_analysis/output' if base_dir.is_relative_to(FA_OUTPUT_DIR) else 'output/_基准库'}）"}


# ---------- 基准上下文（供 LLM 复核） ----------
def build_base_ctx(bl: dict) -> str:
    L = [f"## 核查基准（原始财报，{bl['period']}，金额单位：元；来源 {bl['source']}）"]
    for k in ["审计意见类型", "归母净利润", "扣非归母净利润", "非经常性损益合计", "现金分红"]:
        if bl["key_info"].get(k):
            L.append(f"- {k}: {bl['key_info'][k]}")
    for stmt, title in [("资产负债表", "合并资产负债表（期末）"), ("利润表", "合并利润表（本期）"),
                        ("现金流量表", "合并现金流量表（本期）")]:
        if bl["d"].get(stmt):
            L.append(f"### {title}")
            L += [f"- {k}: {v:,.2f}" for k, v in bl["d"][stmt].items()]
    L.append("\n## 8项比率指标基准（规则§二；比率=小数，同比pp=百分点）")
    for period in sorted(bl["indicators"]):
        for ind in INDICATORS_8:
            if ind in bl["indicators"][period]:
                L.append(f"- {period} {ind}: {bl['indicators'][period][ind]:.6f}")
    return "\n".join(L)


# ---------- 全口径声明：单季/预测防误报过滤 ----------
SCOPE_HINT = re.compile(r"Q[1-4]|[一二三四]季度|单季|预测|预计|预估|分别为|2026E|目标价|[2-9]0(2[6-9]|3\d)E")
def scope_full_claim(claim: dict) -> str:
    return "跳过" if SCOPE_HINT.search(str(claim.get("原文", ""))) else "参与"


# ---------- 8项指标 → s3 行结构 ----------
# 研报常把事业部/板块/产品线毛利率与合并口径并列，命中即判口径差异（不误报为数值错误）
SEGMENT_WORDS = re.compile(r"业务|事业部|板块|产品线|系列|子公司|子行业|牛黄|Chemistry|Testing|工业|商业|分品类|细分|销售结构|收入|HKSCC|NOMINEES|分部")
def indicator_rows(comp: str, claims, indicators: dict, sources: dict, log=print) -> list[dict]:
    claims = step3a_scope.scope_claims(claims)
    compared = cmp.compare_all(claims, indicators, tol=NUM_TOL, pp_tol=PP_TOL)
    compared = cmp.apply_rounding_review(compared)
    rows, seq = [], 1
    for c in compared:
        c["基准来源"] = sources.get((comp, c["报告期"]), "") or "基准库 03_指标结果.csv"
        verdict = c.get("判定", "")
        if verdict == "一致":
            concl, etype, lvl = "正确", "", ""
        elif verdict == "附加观察":
            concl, etype, lvl = "待复核", "附加指标口径", ""
        elif verdict == "疑似错误":
            if SEGMENT_WORDS.search(str(c.get("原文", ""))):
                concl, etype, lvl = "待复核", "口径差异", ""
            else:
                concl, etype, lvl = "错误", c.get("差异类型", "数值不一致"), "中"
        elif verdict == "无法核实":
            concl, etype, lvl = "无法核实", "", ""
        else:  # 误报剔除 / 口径差异
            concl, etype, lvl = ("待复核" if verdict == "误报剔除" else "口径差异"), "", ""
        rows.append({
            "行号": f"指标{seq}", "声明类型": "指标", "基准键": c["指标"],
            "报告期": c.get("报告期", ""), "原文": c["原文"],
            "草稿数值_元": c["数值"], "草稿单位": "",
            "基准数值_元": c.get("基准值"), "基准来源": c["基准来源"],
            "差异率": c.get("差异率", ""), "核查结论": concl, "错误类型": etype,
            "等级": lvl, "核查说明": c.get("说明", ""),
            "修改建议": c.get("修改建议", ""),
        })
        seq += 1
    n = sum(1 for r in rows if r["核查结论"] == "错误")
    log(f"[指标] 8项比率比对 {len(rows)} 条：疑似错误 {n}，"
        f"跳过留痕 {sum(1 for c in claims if c['类别'] != '参与比对')}")
    return rows


def main():
    log_lines = []
    def log(m):
        print(m); log_lines.append(m)

    # --init / --clean / --clear：初始化——清空 output/ 下全部内容后重新核查
    if any(f in sys.argv for f in ("--init", "--clean", "--clear")):
        init_output(log)

    OUTPUT_DIR.mkdir(exist_ok=True)

    summary = []
    try:
        # 基准库公司清单（short name 集合）
        known = set()
        for root, prefix in [(FA_OUTPUT_DIR, ""), (OUTPUT_DIR, "_基准库_")]:
            if root.is_dir():
                for d in root.iterdir():
                    if d.is_dir():
                        nm = d.name[len(prefix):] if prefix and d.name.startswith(prefix) else d.name
                        known.add(short_name(nm))
        if not known:
            log("[中止] output 中无财报分析基准（financial_analysis/output 与 _基准库 均为空）")
            return

        # ① 草稿 = files/ 下的研究报告
        drafts = sorted(p for p in FILES_DIR.iterdir()
                        if p.suffix.lower() in DRAFT_EXTS and "研报" in p.stem)
        if not drafts:
            log(f"[中止] {FILES_DIR} 中没有研究报告PDF（请只放研报）")
            return
        for draft in drafts:
            draft_name = draft.stem
            try:
                log(f"===== 核查研报：{draft.name} =====")
                # ② 识别公司并加载 output 中的财报分析基准
                comp, _pages, ind_claims_all = ind_claims.parse_draft(draft, sorted(known))
                if comp is None:
                    comp = next((k for k in sorted(known) if draft_name.startswith(k)), None)
                if comp is None:
                    log(f"[中止] 未能从研报识别公司（基准库公司：{sorted(known)}）")
                    summary.append({"研报": draft_name, "公司": "", "错误数": "", "状态": "未识别公司"})
                    continue
                bl = load_company_baseline(comp, log)
                if bl is None:
                    summary.append({"研报": draft_name, "公司": comp, "错误数": "", "状态": "无基准"})
                    continue

                paths = config.make_report_paths(comp)   # output/<公司>/
                paths.mkdir()

                # ③ 全口径核查（step3_verify）
                lines, claims, cites = parse_draft(draft)
                paths.DRAFT_TXT.write_text("\n".join(lines), encoding="utf-8")
                claims.to_csv(paths.CLAIMS_CSV, index=False, encoding="utf-8-sig")
                log(f"[draft] 抽取声明 {len(claims)} 条；引用 {len(cites)} 条")

                # 单季/预测声明防误报：不参与自动比对，仅留痕
                scoped = claims.copy()
                scoped["范围"] = [scope_full_claim(c) for _, c in claims.iterrows()]
                scoped.to_csv(paths.CLAIMS_CSV, index=False, encoding="utf-8-sig")
                claims_participate = scoped[scoped["范围"] == "参与"]
                claims_skip = scoped[scoped["范围"] == "跳过"]
                for _, c in claims_skip.iterrows():
                    log(f"[范围] 跳过单季/预测声明：行{c['行号']}｜{str(c['原文'])[:40]}")
                rows = s3.verify(claims_participate, bl["d"], bl["key_info"], lines)
                rows += [{"行号": "-", "声明类型": "审计", "基准键": "审计意见", "原文": a["说明"],
                          "草稿数值_元": None, "基准数值_元": None, "基准来源": "01_关键信息.csv",
                          "差异率": "", "核查结论": "提示", "错误类型": "审计意见",
                          "等级": a["等级"], "核查说明": a["说明"]}
                         for a in s3.audit_opinion_alert(bl["key_info"])]
                # 被范围过滤的单季/预测声明：登记为待复核留痕（防误报）
                for _, c in claims_skip.iterrows():
                    rows.append({"行号": c["行号"], "声明类型": c["声明类型"], "基准键": c["基准键"],
                                 "原文": c["原文"], "草稿数值_元": c["草稿数值_元"], "草稿单位": c["草稿单位"],
                                 "基准数值_元": None, "基准来源": "", "差异率": "",
                                 "核查结论": "待复核", "错误类型": "单季/预测口径",
                                 "等级": "", "核查说明": "单季/预测口径，未与年报基准自动比对（避免误报）"})

                # ④ 8项比率指标比对（规则文档）
                rows += indicator_rows(comp, ind_claims_all, bl["indicators"], {}, log)

                pd.DataFrame(rows).to_csv(paths.VERIFY_CSV, index=False, encoding="utf-8-sig")
                n_err = sum(1 for r in rows if r["核查结论"] == "错误")
                log(f"[verify] 结论 {len(rows)} 条：错误 {n_err}；"
                    f"待复核/无法核实 {sum(1 for r in rows if r['核查结论'] in ('待复核','无法核实'))}")

                base_ctx = build_base_ctx(bl)
                fix_rows = [r for r in rows if r["核查结论"] in ("错误", "待复核")]
                llm_md = s4.generate_fixes(fix_rows, cites, base_ctx, log)
                pd.DataFrame([{"建议": llm_md}]).to_csv(paths.FIX_CSV, index=False,
                                                        encoding="utf-8-sig")

                # ⑤ 核查报告 MD
                report_md = build_report(comp, draft, bl, rows, cites, llm_md, paths)
                paths.REPORT_MD.write_text(report_md, encoding="utf-8")

                paths.EVIDENCE_JSON.write_text(json.dumps(
                    {"基准": bl["source"], "基准目录": str(bl["base_dir"]), "报告期": bl["period"],
                     "科目证据": bl["evid"], "关键信息": bl["key_info"]},
                    ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                s5.build(paths, rows, cites, s3.audit_opinion_alert(bl["key_info"]),
                         llm_md, draft.name)
                log(f"[done] {draft_name}：错误{n_err} → output/{paths.report_name}/07_核查报告.md")
                summary.append({"研报": draft.name, "公司": comp, "错误数": n_err, "状态": "完成"})
            except Exception:
                log(f"[研报失败] {draft_name}\n" + traceback.format_exc())
                summary.append({"研报": draft_name, "公司": "", "错误数": "", "状态": "处理失败"})
        pd.DataFrame(summary).to_csv(OUTPUT_DIR / "00_批处理汇总.csv", index=False,
                                     encoding="utf-8-sig")
        log(f"[all done] 共核查 {len(summary)} 份研报；汇总见 00_批处理汇总.csv")
    except Exception:
        log("流程异常：\n" + traceback.format_exc())
    finally:
        (OUTPUT_DIR / "run.log").write_text("\n".join(log_lines), encoding="utf-8")


def _f(v):
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return "-"


def _ind_fmt(ind, v) -> str:
    """指标值展示：比率/百分比类显示百分比，同比pp显示pct，其余保留数值。"""
    if v is None:
        return "-"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if ind == "毛利率同比pp":
        return f"{v:+.2f}pct"
    if ind in ("资产负债率", "现金流量比率", "营业现金比率", "资本积累率", "毛利率"):
        return f"{v * 100:.2f}%"
    return f"{v:.2f}"


def build_report(comp, draft, bl, rows, cites, llm_md, paths) -> str:
    errs = [r for r in rows if r["核查结论"] == "错误"]
    soft = [r for r in rows if r["核查结论"] in ("待复核", "无法核实")]
    ind_rows = [r for r in rows if r["声明类型"] == "指标"]
    L = [f"# 研究报告纠错核查报告\n",
         f"## 核查对象\n- 研报：`{draft.name}`",
         f"- 公司：{comp}（基准 {bl['period']}，来源：{bl['source']}）",
         f"- 依据：《研报财务数据比对判定规则.docx》（8项比率指标、容差≤0.5%）"
         f"及原任务（收入/利润/估值/单位/统计口径/引用）\n",
         "## 一、比对结果汇总\n",
         f"- 全口径声明：**{len([r for r in rows if r['声明类型'] != '指标'])}** 条"
         f"（其中错误 **{len(errs)}** 条，待复核/无法核实 **{len(soft)}** 条）",
         f"- 指标声明比对：**{len(ind_rows)}** 条（错误 "
         f"**{sum(1 for r in ind_rows if r['核查结论']=='错误')}** 条，含8项基准比率与附加指标口径观察）",
         f"- **疑似错误合计：{len(errs)} 条**\n",
         "### 指标核对总览\n",
         "| 指标 | 报告期 | 研报值 | 基准值 | 核对结论 | 修改建议/说明 |",
         "|---|---|---|---|---|---|"]
    declared_inds: set = set()
    for r in ind_rows:
        declared_inds.add(r["基准键"])
        note = (r.get("修改建议") or r.get("核查说明") or "")
        L.append(f"| {r['基准键']} | {r.get('报告期','')} | {_ind_fmt(r['基准键'], r['草稿数值_元'])} | "
                 f"{_ind_fmt(r['基准键'], r['基准数值_元'])} | {r['核查结论']}"
                 f"{('·' + str(r['错误类型'])) if r['错误类型'] else ''} | {str(note)[:120]} |")
    # 研报未披露、但基准库已有的8项指标（覆盖缺口提示）
    for ind in INDICATORS_8:
        if ind in declared_inds:
            continue
        base_v = bl.get("indicators", {}).get(bl.get("period", ""), {}).get(ind)
        if base_v is None:
            continue
        L.append(f"| {ind} | {bl.get('period','')} | 研报未披露 | {_ind_fmt(ind, base_v)} | "
                 f"未使用 | 该指标研报未声明，未纳入比对（覆盖缺口，可人工补核） |")
    if not ind_rows:
        L.append("| （无8项指标声明） | | | | | |")
    L += ["\n## 二、错误清单\n",
         "| 行号 | 类型 | 等级 | 原文(截断) | 草稿值(元) | 基准值(元) | 来源 | 说明 |",
         "|---|---|---|---|---:|---:|---|---|"]
    for r in errs:
        L.append(f"| {r['行号']} | {r['错误类型']} | **{r['等级']}** | {str(r['原文'])[:40]} | "
                 f"{_f(r['草稿数值_元'])} | {_f(r['基准数值_元'])} | {r['基准来源']} | {r['核查说明']} |")
    if not errs:
        L.append("| （无） | | | | | | | |")
    L += ["\n## 三、待复核/无法核实（不判错，人工复核；含单季/预测留痕、附加指标口径与误报剔除）\n"]
    L += [f"- 行{r['行号']}｜{r['基准键']}｜{str(r['核查说明'])[:100]}" for r in soft] or ["（无）"]
    L += ["\n## 四、修改建议（LLM 复核）\n", llm_md,
          "\n## 五、证据链\n",
          f"- 基准来源：{bl['source']}（报告期 {bl['period']}）",
          *[f"- {k}: {v}" for k, v in list(bl["evid"].items())[:40]]]
    # 六、附加观察（按本报告实际发现动态生成，避免串入他公司文本）
    obs = ["\n## 六、附加观察（超出自动比对范围，仅提示）\n"]
    kou = [r for r in errs if r["错误类型"] == "口径错误"]
    if kou:
        obs.append("- 本次核查发现 " + str(len(kou)) + " 处疑似统计口径混用（见错误清单/修改建议），涉及基准键："
                   + "、".join(str(r["基准键"]) for r in kou) + "。")
    scoped_n = sum(1 for r in soft if "单季/预测口径" in str(r["核查说明"]))
    if scoped_n:
        obs.append(f"- {scoped_n} 条单季/预测口径声明未与年报基准自动比对（避免误报），留痕备查。")
    extra_ind = [r for r in ind_rows if r["错误类型"] == "附加指标口径"]
    if extra_ind:
        obs.append("- 研报披露了规则8项基准之外的附加指标（"
                   + "、".join(dict.fromkeys(str(r["基准键"]) for r in extra_ind))
                   + "），属数据终端衍生指标，计算口径与年报基准及不同终端可能不一致"
                   "（如『速动比率』标签是否实为『保守速动比率』），需人工核对口径与标签定义，详见『指标核对总览』。")
    if len(obs) == 1:
        obs.append("- 无超出自动比对范围的特殊观察。")
    L += obs
    L += ["\n## 七、质量自评与风险说明\n",
          "- 质量自评见 `08-1_质量评分.csv`（来源定位率、建议可执行率、待复核占比、核查维度覆盖）。",
          "- 风险：① 单季/预测声明已按范围过滤避免误报；② 可比期基准由年报上期数计算；"
          "③ 估值倍数重算受草稿市值/股本取数日期影响，容差 2%。"]
    return "\n".join(L)


if __name__ == "__main__":
    main()
