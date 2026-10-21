from __future__ import annotations
import re
from decimal import Decimal, ROUND_HALF_UP

from config import NUM_TOL, PP_TOL, INDICATORS_8

# 附加指标（不在规则§二8项基准内，如每股经营现金流/速动比率/ROE）统一口径提醒
EXTRA_NOTE = ("该指标不在规则8项基准内，属研报/数据终端衍生指标（如Wind、同花顺按自身口径计算的"
              "'速动比率/每股经营现金流'等）。其计算口径与年报披露基准可能不同（含/不含存货、货币资金定义、"
              "每股股本基期等），且不同终端衍生口径也可能不一致，需人工核对数据源口径并核实指标标签定义"
              "（如表格标注『速动比率』实际是否按『保守速动比率』计算）后再判定。")

# 展示风格
PCT_STYLE  = {"资产负债率", "现金流量比率", "营业现金比率", "资本积累率", "毛利率"}
PP_STYLE   = {"毛利率同比pp"}
MULT_STYLE = {"流动比率", "产权比率"}


def fmt_value(ind: str, v) -> str:
    if v is None:
        return "-"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if ind in PP_STYLE:
        return f"{v:+.2f}pct"
    if ind in PCT_STYLE:
        return f"{v * 100:.2f}%"
    return f"{v:.2f}"


def is_close(val: float, base: float, ind: str,
             tol: float = NUM_TOL, pp_tol: float = PP_TOL) -> tuple[bool, float | None]:
    """返回 (是否一致, 差异度量)。比率/倍数类用相对误差；同比pp用绝对误差。"""
    if ind in PP_STYLE:
        return abs(val - base) <= pp_tol, abs(val - base)
    if base == 0:
        return val == 0, None
    diff = abs(val - base) / abs(base)
    return diff <= tol, diff


def compare_one(claim: dict, base_indicators: dict, ind: str | None = None,
                val: float | None = None, period: str | None = None,
                tol: float = NUM_TOL, pp_tol: float = PP_TOL) -> dict:
    """单条参与比对声明的比对。

    base_indicators: {(报告期): {指标: 数值}}（同一公司）。
    返回字段：判定 / 差异类型 / 研报值 / 基准值 / 基准报告期 / 差异率 / 说明 / 修改建议。
    """
    ind = ind or claim["指标"]
    val = claim["数值"] if val is None else val
    period = period or claim["报告期"]
    if ind not in INDICATORS_8:
        return {"判定": "附加观察", "差异类型": "附加指标口径", "研报值": val,
                "基准值": None, "基准报告期": period, "差异率": "",
                "说明": EXTRA_NOTE, "修改建议": "研报值保留；核对数据源口径与指标标签定义后决定是否修正（口径需人工确认）。"}
    base = base_indicators.get(period, {}).get(ind)
    res = {"判定": "", "差异类型": "", "研报值": val, "基准值": None,
           "基准报告期": period, "差异率": "", "说明": "", "修改建议": ""}

    if base is None:
        # 年份不一致候选：本期无基准，但数值命中相邻年份基准
        m = re.search(r"(20\d{2})", period)
        claim_year = int(m.group(1)) if m else None
        hits = []
        for p2, inds in base_indicators.items():
            if ind not in inds:
                continue
            b2 = inds[ind]
            ok, _ = is_close(val, b2, ind, tol, pp_tol)
            if ok:
                hits.append((p2, b2))
        if len(hits) == 1:
            p2, b2 = hits[0]
            m2 = re.search(r"(20\d{2})", p2)
            if claim_year and m2 and int(m2.group(1)) != claim_year:
                res.update({
                    "判定": "疑似错误", "差异类型": "年份不一致",
                    "基准值": b2, "基准报告期": p2,
                    "说明": f"研报标注『{period}』，但数值命中『{p2}』基准",
                    "修改建议": f"将『{ind}』标注年份由 {claim_year} 更正为 {m2.group(1)}"
                                f"（数值 {fmt_value(ind, val)} 对应 {p2} 基准）"})
                return res
        res.update({"判定": "无法核实",
                    "说明": f"基准库无『{period}』的『{ind}』，无法比对"})
        return res

    res["基准值"] = base
    ok, diff = is_close(val, base, ind, tol, pp_tol)
    if ind in PP_STYLE:
        res["差异率"] = f"{diff:.4f}pp" if diff is not None else "-"
    else:
        res["差异率"] = f"{diff:.4%}" if diff is not None else "-"
    if ok:
        res["判定"] = "一致"
        res["说明"] = f"差异在容差内（{'绝对' if ind in PP_STYLE else '相对'}）"
    else:
        res["判定"] = "疑似错误"
        res["差异类型"] = "数值不一致"
        res["说明"] = f"研报值 {fmt_value(ind, val)} vs 基准值 {fmt_value(ind, base)}" \
                      f"（差异{'绝对' if ind in PP_STYLE else '相对'} {diff:.4%}，超容差）"
        res["修改建议"] = (f"将『{ind}』由 {fmt_value(ind, val)} 更正为 "
                          f"{fmt_value(ind, base)}（基准来源见证据链）")
    return res


def compare_all(claims: list[dict], base_indicators: dict,
                tol: float = NUM_TOL, pp_tol: float = PP_TOL) -> list[dict]:
    """对每条『参与比对』声明执行比对，返回含比对结果字段的字典列表。"""
    out = []
    for c in claims:
        if c.get("类别") != "参与比对":
            continue
        r = dict(c)
        r.update(compare_one(c, base_indicators, tol=tol, pp_tol=pp_tol))
        out.append(r)
    return out


# ---------- 复核：研报披露精度导致的舍入（规则§4.3） ----------
def _display_precision(val_raw) -> int | None:
    """从数值原文提取显示小数位，如 '4.5'→1、'91.18%'→2、'-0.75pct'→2。"""
    m = re.search(r"[-+]?\d+(\.\d+)?", str(val_raw))
    if not m:
        return None
    frac = m.group(1)
    return len(frac) - 1 if frac else 0


def _round_display(base: float, precision: int) -> float:
    """按给定小数位做四舍五入（ROUND_HALF_UP，避免浮点银行家舍入歧义）。"""
    q = Decimal("0." + "0" * precision) if precision > 0 else Decimal("1")
    return float(Decimal(str(base)).quantize(q, rounding=ROUND_HALF_UP))


def apply_rounding_review(compared: list[dict]) -> list[dict]:
    """确定性复核闸门：机械比对判『数值不一致』的行，若研报值恰为基准值
    按研报披露精度（数值原文小数位）舍入所得 → 判『误报剔除』留痕（§五），
    不计入错误统计。对应规则§4.3『研报披露精度导致的舍入』。"""
    for c in compared:
        if c.get("判定") != "疑似错误" or c.get("差异类型") != "数值不一致":
            continue
        base = c.get("基准值")
        claim = c.get("研报值")
        if base is None or claim is None:
            continue
        prec = _display_precision(c.get("数值原文", ""))
        if prec is None:
            continue
        shown = prec
        if c["指标"] in PCT_STYLE:
            prec += 2            # 百分比显示 19.0% → 存储小数 0.190
        rounded = _round_display(base, prec)
        if abs(claim - rounded) <= 1e-9:
            c["判定"] = "误报剔除"
            c["说明"] = (f"研报披露精度为{shown}位小数，研报值 {fmt_value(c['指标'], claim)} "
                         f"为基准值 {fmt_value(c['指标'], base)} 按该精度规范舍入的结果"
                         f"（规则§4.3舍入容差），判定误报剔除、留痕不计错")
            c["修改建议"] = "无需修改（数值为基准值的规范舍入）"
    return compared
