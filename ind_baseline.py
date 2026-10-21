from __future__ import annotations
import re
from pathlib import Path
import pandas as pd

import config
from config import FA_OUTPUT_DIR, FA_META_CSV, OUTPUT_DIR, INDICATORS_8

# 本地基准的科目→PDF位置证据（{公司: evid}），供 06_证据链.json 引用
_LOCAL_EVID: dict[str, dict] = {}


# ---------- 公司简称 ----------
def short_name(folder_or_file: str) -> str:
    """'中芯国际：2025年年度报告' → '中芯国际'；兼容半角冒号。"""
    s = Path(folder_or_file).stem
    return re.split(r"[：:]", s)[0].strip()


def _norm_period(year, period: str) -> str | None:
    """(2025, '年报') → '2025年年报'；也容忍 csv 里已是 '2025年年报' 的写法。"""
    if year is None and period:
        m = re.search(r"(20\d{2})", str(period))
        if m:
            year = int(m.group(1))
    if year is None:
        return None
    p = str(period or "年报")
    if not p.startswith(str(year)):
        p = f"{year}年{p}"
    return p


# ---------- 来源 A：financial_analysis 产出（只读） ----------
def load_fa_baselines(log=print) -> dict:
    base: dict[str, dict[str, dict[str, float]]] = {}
    if not FA_OUTPUT_DIR.is_dir():
        log(f"[基准] ⚠️ 未找到 {FA_OUTPUT_DIR}，跳过 financial_analysis 基准")
        return base
    n = 0
    for f in sorted(FA_OUTPUT_DIR.glob("*/03_指标结果.csv")):
        comp = short_name(f.parent.name)
        try:
            df = pd.read_csv(f, encoding="utf-8-sig")
        except Exception as exc:
            log(f"[基准] ⚠️ 读取失败 {f.name}: {exc}")
            continue
        for _, r in df.iterrows():
            ind = str(r.get("指标", "")).strip()
            if ind not in INDICATORS_8:
                continue
            period = str(r.get("报告期", "")).strip()
            try:
                val = float(str(r.get("数值", "")).replace(",", ""))
            except ValueError:
                continue
            base.setdefault(comp, {}).setdefault(period, {})[ind] = val
            n += 1
    log(f"[基准] financial_analysis 只读加载：{len(base)} 家公司，{n} 条指标（来源 {FA_OUTPUT_DIR}）")
    return base


# ---------- 来源 B：本地兜底，从年报PDF计算 ----------
# 规则§二公式与 step2_standardize.assemble() 键的对照：
#   资产负债表: {科目}_期末/{科目}_期初；利润表: {科目}_本期/{科目}_上期；现金流量表: {科目}
def compute_8_indicators(d: dict) -> dict:
    """d 为 assemble() 的三表字典，返回 {指标: 数值}（比率=小数，同比pp=百分点）。"""
    bs, ps, cf = d.get("资产负债表", {}), d.get("利润表", {}), d.get("现金流量表", {})
    out = {}

    def _div(a, b, scale=1.0):
        return a / b * scale if (a is not None and b) else None

    ca_end   = bs.get("流动资产_期末")
    cl_end   = bs.get("流动负债_期末")
    tl_end   = bs.get("总负债_期末")
    ta_end   = bs.get("总资产_期末")
    eq_end   = bs.get("所有者权益_期末")
    eq_beg   = bs.get("所有者权益_期初")
    rev_cur  = ps.get("营业收入_本期")
    cost_cur = ps.get("营业成本_本期")
    rev_pre  = ps.get("营业收入_上期")
    cost_pre = ps.get("营业成本_上期")
    ocf      = cf.get("经营活动现金净流量")

    v = _div(ca_end, cl_end)
    if v is not None: out["流动比率"] = v
    v = _div(ocf, cl_end)
    if v is not None: out["现金流量比率"] = v
    v = _div(tl_end, ta_end)
    if v is not None: out["资产负债率"] = v
    v = _div(tl_end, eq_end)
    if v is not None: out["产权比率"] = v
    gm_cur = _div((rev_cur - cost_cur) if (rev_cur is not None and cost_cur is not None) else None, rev_cur)
    if gm_cur is not None: out["毛利率"] = gm_cur
    gm_pre = _div((rev_pre - cost_pre) if (rev_pre is not None and cost_pre is not None) else None, rev_pre)
    if gm_cur is not None and gm_pre is not None:
        out["毛利率同比pp"] = (gm_cur - gm_pre) * 100.0   # 与FA口径一致：百分点数
    v = _div(ocf, rev_cur)
    if v is not None: out["营业现金比率"] = v
    v = _div((eq_end - eq_beg) if (eq_end is not None and eq_beg is not None) else None, eq_beg)
    if v is not None: out["资本积累率"] = v
    return out


def compute_8_indicators_prior(d: dict) -> dict:
    """由上期/期初科目计算上一报告期（如 2024A）可比的 8 项指标。
    年报附列上期三表数，与上一份年报口径一致（经人工抽检交叉验证）。
    现金流量表未保留上期经营现金流 → 现金流量比率/营业现金比率缺；
    资本积累率需再上一期权益 → 缺；毛利率同比pp 需再上期毛利率 → 缺。"""
    bs, ps = d.get("资产负债表", {}), d.get("利润表", {})
    out = {}

    def _div(a, b, scale=1.0):
        return a / b * scale if (a is not None and b) else None

    ca = bs.get("流动资产_期初")
    cl = bs.get("流动负债_期初")
    tl = bs.get("总负债_期初")
    ta = bs.get("总资产_期初")
    eq = bs.get("所有者权益_期初")
    rev = ps.get("营业收入_上期")
    cost = ps.get("营业成本_上期")
    if ca is not None and cl:
        out["流动比率"] = ca / cl
    if tl is not None and ta:
        out["资产负债率"] = tl / ta
    if tl is not None and eq:
        out["产权比率"] = tl / eq
    if rev and cost is not None:
        out["毛利率"] = (rev - cost) / rev
    return out


def compute_local_baseline(pdf: Path, log=print) -> tuple[str, dict, dict] | None:
    """从单份年报PDF计算8项指标（本期+上期）；中间产物与 03_指标结果.csv 缓存到 output\\_基准库_*。
    返回 (公司简称, {报告期: {指标: 数值}}, 科目证据)；失败返回 None。"""
    import extract_financial_datav2 as v2
    import step2_standardize as s2

    stem = pdf.stem
    comp = short_name(stem)
    paths = config.make_report_paths("_基准库_" + stem)
    paths.mkdir()
    try:
        rows, _audits = v2.extract_from_pdf(pdf)
    except Exception as exc:
        log(f"[基准] 本地解析失败 {pdf.name}: {exc}")
        return None
    raw = pd.DataFrame(rows, columns=v2.OUTPUT_FIELDS)
    raw.to_csv(paths.RAW_CSV, index=False, encoding="utf-8-sig")
    std = s2.run(raw, log, paths)
    if std.empty:
        log(f"[基准] ⚠️ {stem} 标准化为空，无法计算本地基准")
        return None
    companies, _cont = s2.assemble(std)
    if not companies:
        return None
    _full, files = next(iter(companies.items()))
    fname, it = next(iter(files.items()))
    ind_cur = compute_8_indicators(it["d"])
    period = _norm_period(it["year"], it["period"])
    if not ind_cur or period is None:
        log(f"[基准] ⚠️ {stem} 指标计算为空或报告期不明")
        return None
    by_period = {period: ind_cur}
    prior = compute_8_indicators_prior(it["d"])
    if prior:
        m = re.search(r"(20\d{2})", period)
        if m:
            by_period[f"{int(m.group(1))-1}年年报"] = prior
    df = pd.DataFrame([{"公司": comp, "报告期": p, "指标": k, "数值": v}
                       for p, inds in by_period.items() for k, v in inds.items()])
    df.to_csv(paths.dir / "03_指标结果.csv", index=False, encoding="utf-8-sig")
    log(f"[基准] 本地计算 {comp} {period}：{len(ind_cur)}/8 项指标 → "
        f"{paths.dir.name}\\03_指标结果.csv（另含上期可比 {'、'.join(by_period) if len(by_period)>1 else ''}）")
    return comp, by_period, it["evid"]


def build_baselines(files_dir: Path, log=print) -> dict:
    """汇总全部基准：A（financial_analysis，只读）优先，B（本地年报PDF）补缺。
    返回 {(公司简称, 报告期): {指标: 数值}} 双层键（公司, 报告期），
    并附带 _LOCAL_EVID 记录本地基准的科目页码证据。"""
    base = {}
    for comp, by_p in load_fa_baselines(log).items():
        for p, inds in by_p.items():
            base[(comp, p)] = dict(inds)
    if files_dir.is_dir():
        for pdf in sorted(files_dir.glob("*.pdf")):
            s = pdf.stem
            if "研报" in s:
                continue                      # 研报是草稿，不是基准
            if "年度报告" not in s and "年报" not in s:
                continue
            got = compute_local_baseline(pdf, log)
            if not got:
                continue
            comp, by_period, evid = got
            for period, inds in by_period.items():
                slot = base.setdefault((comp, period), {})
                miss = [k for k in INDICATORS_8 if k not in slot]
                for k in miss:
                    if k in inds:
                        slot[k] = inds[k]
                if miss:
                    log(f"[基准] {comp} {period} 本地补缺 {len(miss)} 项：{'、'.join(miss)}")
            _LOCAL_EVID[(comp, "本地年报")] = evid
    return base


# ---------- 公司别名（供草稿归属识别） ----------
def known_company_names(base: dict) -> list[str]:
    names = set()
    for k in base:
        names.add(k[0] if isinstance(k, tuple) else k)
    if FA_META_CSV.exists():
        try:
            df = pd.read_csv(FA_META_CSV, encoding="utf-8-sig")
            col = df.columns[0]
            names |= {str(x).strip() for x in df[col].dropna()}
        except Exception:
            pass
    # 长的优先，避免“中芯”截胡“中芯国际”
    return sorted(names, key=len, reverse=True)
