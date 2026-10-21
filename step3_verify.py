import math
import pandas as pd
from config import NUM_TOL, NUM_SOFT, MULT_TOL, UNIT_TOL, SEVERE_HIGH, SEVERE_MID

BASELINE = {   # 基准键 → (报表, 科目后缀) ；key_info 键单独处理
    "营业收入": ("利润表", "营业收入_本期"), "营业成本": ("利润表", "营业成本_本期"),
    "净利润": ("利润表", "净利润_本期"),   "利润总额": ("利润表", "利润总额_本期"),
    "经营活动现金净流量": ("现金流量表", "经营活动现金净流量"),
    "总资产": ("资产负债表", "总资产_期末"), "总负债": ("资产负债表", "总负债_期末"),
    "所有者权益": ("资产负债表", "所有者权益_期末"),
}
KEYINFO_KEYS = {"归母净利润", "扣非归母净利润"}
UNIT_STEPS = [("亿元", 1e8), ("百万元", 1e6), ("万元", 1e4), ("千元", 1e3)]

def _get_baseline(key: str, d: dict, key_info: dict):
    if key in KEYINFO_KEYS:
        v = key_info.get(key, "")
        try:
            return float(str(v).replace(",", "")), f"关键信息[{key}]"
        except Exception:
            return None, ""
    if key in BASELINE:
        stmt, suffix = BASELINE[key]
        v = d.get(stmt, {}).get(suffix)
        return (float(v), f"{stmt}·{suffix}") if v is not None else (None, "")
    if key in ("市盈率", "市净率", "市销率", "目标价", "总市值", "股价", "总股本"):
        return None, ""   # 行情类无财报基准，走倍数重算/无法核实
    return None, ""

def _grade(diff: float) -> str:
    return "高危" if diff > SEVERE_HIGH else ("中" if diff > SEVERE_MID else "低")

def _rec(claim, conclusion, err_type="", level="", base=None, src="", note=""):
    return {"行号": claim["行号"], "声明类型": claim["声明类型"], "基准键": claim["基准键"],
            "原文": claim["原文"], "草稿数值_元": claim["草稿数值_元"],
            "基准数值_元": base, "基准来源": src, "差异率": "",
            "核查结论": conclusion, "错误类型": err_type, "等级": level, "核查说明": note}

def verify(claims: pd.DataFrame, d: dict, key_info: dict, draft_lines: list[str]) -> list[dict]:
    R = []
    if claims is None or not len(claims):
        return R
    # 各基准键参与的数字声明数（>1 视为拆分明细/多口径/预测混排，无法确证为错误 → 降级待复核）
    key_cnt: dict = {}
    for _, c in claims[claims["声明类型"] == "数字"].iterrows():
        if pd.notna(c["草稿数值_元"]) and c["基准键"]:
            key_cnt[c["基准键"]] = key_cnt.get(c["基准键"], 0) + 1
    # A/B/C/D 逐条声明核查
    for _, c in claims.iterrows():
        base, src = _get_baseline(c["基准键"], d, key_info)
        v = c["草稿数值_元"]
        if pd.isna(v) or v is None:
            R.append(_rec(c, "待复核", note="草稿该句未提取到数值，请人工确认"))
            continue
        if c["声明类型"] == "倍数":
            pe = _recalc_pe(c, claims, d, key_info)
            if pe is None:
                R.append(_rec(c, "无法核实", note="缺少重算所需的市值/股本/利润基准"))
                continue
            implied, calc_src = pe
            diff = abs(v - implied) / abs(implied) if implied else 1
            if diff <= MULT_TOL:
                R.append(_rec(c, "正确", base=implied, src=calc_src,
                              note=f"重算值{implied:.2f}，容差{MULT_TOL:.0%}内"))
            else:
                # PE 依赖市值取数日期 + 净利润口径（IFRS/Non-IFRS、归母/合并），
                # 无法确证为研报错误 → 待人工复核（误报控制）
                R.append(_rec(c, "待复核", base=implied, src=calc_src,
                              note=f"重算值{implied:.2f} vs 草稿{v:.2f}，差异{diff:.2%}"
                                   f"（市值取数日期/净利润口径存疑），待人工复核"))
            continue
        if base is None or (isinstance(base, float) and math.isnan(base)):
            R.append(_rec(c, "无法核实", note=f"基准侧未找到『{c['基准键']}』，请补充原始材料"))
            continue
        diff = abs(v - base) / abs(base)
        # C) 口径甄别：先试其它口径基准
        alt = _alt_key(c["基准键"])
        alt_v, alt_src = _get_baseline(alt, d, key_info) if alt else (None, "")
        base_out, src_out = base, src
        if diff <= NUM_TOL:
            verdict, etype, lvl = "正确", "", ""
            note = f"差异{diff:.4%}，容差内"
        elif alt_v is not None and not (isinstance(alt_v, float) and math.isnan(alt_v)) \
                and abs(v - alt_v) / abs(alt_v) <= NUM_SOFT:
            verdict, etype = "错误", "口径错误"
            lvl = _grade(abs(v - alt_v) / abs(alt_v))
            base_out, src_out = alt_v, alt_src
            note = (f"数值命中『{alt}』口径（差异{abs(v - alt_v) / abs(alt_v):.4%}），"
                    f"疑似混用『{c['基准键']}』与『{alt}』")
        elif diff <= NUM_SOFT:
            verdict, etype, lvl = "待复核", "", ""
            note = f"差异{diff:.4%}在{NUM_SOFT:.0%}内，可能为四舍五入/不同版本"
        else:
            # B) 单位换算甄别
            unit_hit = None
            for uname, um in UNIT_STEPS:
                if abs(v * um - base) / abs(base) <= UNIT_TOL or abs(v / um - base) / abs(base) <= UNIT_TOL:
                    unit_hit = uname
                    break
            if unit_hit:
                if c["草稿单位"]:   # 单位在原文中已显式标注且换算吻合 → 单位误用
                    verdict, etype = "错误", "单位错误"
                    lvl = _grade(diff)
                    note = (f"数值按『{unit_hit}』换算后与基准吻合（差异{diff:.2%}），"
                            f"草稿单位『{c['草稿单位']}』误用")
                else:               # 原文未标注单位 → 单位存疑，待人工确认（避免误报）
                    verdict, etype, lvl = "待复核", "", ""
                    note = (f"数值按『{unit_hit}』换算后与基准吻合，但草稿该处未标注单位"
                            f"（疑似{unit_hit}），待人工确认")
            else:
                verdict, etype = "错误", "数字错误"
                lvl = _grade(diff)
                note = f"草稿{v:,.2f}元 vs 基准{base:,.2f}元，差异{diff:.2%}"
        # 同一基准键出现多个数值（拆分明细/多口径/预测混排）→ 无法确证为错误，降级待复核
        if verdict == "错误" and key_cnt.get(c["基准键"], 1) >= 2:
            verdict, etype, lvl = "待复核", "", ""
            note = "同一指标出现多个数值（拆分明细/多口径/预测混排），无法确证为错误，待人工复核：" + note
        R.append(_rec(c, verdict, etype, lvl, base=base_out, src=src_out, note=note))
    return R

def _alt_key(key: str) -> str:
    return {"净利润": "归母净利润", "归母净利润": "净利润",
            "归母净利润": "扣非归母净利润", "扣非归母净利润": "归母净利润"}.get(key, "")

def _recalc_pe(claim, claims: pd.DataFrame, d: dict, key_info: dict):
    """PE重算：优先 草稿市值/归母净利润；其次 股价×股本/归母净利润。
    草稿声明数值已由 _match_value 归一为「元」（单位已换算），此处直接使用。"""
    if claim["基准键"] != "市盈率":
        return None
    # PE 以归母净利润为分母（优先 key_info 归母，其次合并净利润）
    ni = None
    try:
        ni = float(str(key_info.get("归母净利润", "")).replace(",", ""))
    except (TypeError, ValueError):
        ni = None
    if not ni:
        ni = d.get("利润表", {}).get("净利润_本期")
    if not ni:
        return None
    mcap = None
    if claims is not None and len(claims):
        m = claims[(claims["基准键"] == "总市值") & claims["草稿数值_元"].notna()]
        if len(m):
            mcap = float(m.iloc[-1]["草稿数值_元"])
        else:
            p = claims[(claims["基准键"] == "股价") & claims["草稿数值_元"].notna()]
            s = claims[(claims["基准键"] == "总股本") & claims["草稿数值_元"].notna()]
            if len(p) and len(s):
                mcap = float(p.iloc[-1]["草稿数值_元"]) * float(s.iloc[-1]["草稿数值_元"])
    if not mcap:
        return None
    return mcap / ni, f"重算：市值{mcap:,.0f}元 ÷ 净利润{ni:,.0f}元"

def audit_opinion_alert(key_info: dict) -> list[dict]:
    """审计意见非标 → 高危提示（影响草稿所有利润类引用的可信度）"""
    A, audit = [], str(key_info.get("审计意见类型", "") or "")
    if not audit or "标准无保留" in audit:
        return A
    if "无法表示意见" in audit or "否定意见" in audit:
        A.append({"类别": "审计意见", "等级": "高危", "说明":
                  f"基准财报为『{audit}』，其财务数据可靠性严重存疑，草稿引用该财报的数字须整体降级标注"})
    elif "保留意见" in audit or "带强调事项段" in audit:
        A.append({"类别": "审计意见", "等级": "中", "说明":
                  f"基准财报为『{audit}』，草稿引用相关数据时应披露该审计意见"})
    return A
