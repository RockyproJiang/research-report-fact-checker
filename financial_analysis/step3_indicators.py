from config import TOLERANCE_GOV_BOND, VAT_RATE
from step2_standardize import PERIOD_ORDER


def g(d, stmt, key, default=None):
    return d.get(stmt, {}).get(key, default)


IND_CLASS = {
    "流动比率": "偿债能力", "现金流量比率": "偿债能力",
    "资产负债率": "偿债能力", "产权比率": "偿债能力",
    "应收账款周转率同比": "营运能力", "存货周转率同比": "营运能力",
    "总资产周转率同比": "营运能力",
    "毛利率": "盈利能力", "毛利率同比pp": "盈利能力",
    "营业利润率同比pp": "盈利能力",
    "净利率同比pp": "盈利能力", "ROE": "盈利能力",
    "营业现金比率": "现金流质量",
    "营业收入增长率": "成长能力", "净利润增长率": "成长能力",
    "资本积累率": "成长能力",
    "营业收入单季同比": "成长能力", "净利润单季同比": "成长能力",
    "营业收入环比": "成长能力", "净利润环比": "成长能力",
}

THRESHOLDS = [
    ("流动比率", "流动比率", 1.5, 1.0, 0.5, True, 5, "低于1.5预警，低于1异常，低于0.5极度异常"),
    ("资产负债率", "资产负债率", 0.60, 0.70, 0.80, False, 10, "高于60%预警，70%异常，80%极度异常"),
    ("现金流量比率", "现金流量比率", 0.8, 0.5, 0.0, True, 8, "低于0.8预警，0.5异常，低于0极度异常"),
    ("产权比率", "产权比率", 1.0, 2.0, 3.0, False, 7, "高于100%预警，200%异常，300%极度异常；负权益时单独判断"),
    ("应收账款周转率同比", "应收账款周转率同比", -0.20, -0.30, -0.50, True, 8, "同比下降20%预警，30%异常，50%极度异常"),
    ("存货周转率同比", "存货周转率同比", -0.20, -0.30, -0.50, True, 7, "同比下降20%预警，30%异常，50%极度异常"),
    ("总资产周转率同比", "总资产周转率同比", -0.15, -0.25, -0.40, True, 5, "同比下降15%预警，25%异常，40%极度异常"),
    ("毛利率同比pp", "毛利率同比pp", -3, -5, -10, True, 5, "同比下降3pp预警，5pp异常，10pp极度异常"),
    ("营业利润率同比pp", "营业利润率同比pp", -2, -3, -5, True, 5, "同比下降2pp预警，3pp异常，5pp极度异常"),
    ("净利率同比pp", "净利率同比pp", -1.5, -3, -5, True, 8, "同比下降1.5pp预警，3pp异常，5pp极度异常"),
    ("ROE", "ROE", TOLERANCE_GOV_BOND, 0.0, -0.10, True, 10, "低于国债收益率预警，低于0异常，低于-10%极度异常"),
    ("营业现金比率", "营业现金比率", 0.80, 0.50, 0.0, True, 7, "低于80%预警，50%异常，低于0极度异常"),
    ("营业收入增长率", "营业收入增长率", 0.10, 0.0, -0.10, True, 5, "低于10%预警，低于0异常"),
    ("净利润增长率", "净利润增长率", 0.10, 0.0, -0.10, True, 6, "低于10%预警，低于0异常"),
    ("资本积累率", "资本积累率", 0.05, 0.0, -0.10, True, 4, "低于5%预警，低于0异常"),
    ("营业收入单季同比", "营业收入单季同比", 0.10, 0.0, -0.10, True, 5,
     "单季营收同比增速低于10%预警（需上年同季报告）"),
    ("净利润单季同比", "净利润单季同比", 0.10, 0.0, -0.10, True, 6,
     "单季净利润同比增速低于10%预警（需上年同季报告）"),
    ("营业收入环比", "营业收入环比", -0.40, -0.60, -0.80, True, 4,
     "单季营收环比下降40%预警，60%异常（注意季节性与春节错期）"),
]

SEVERITY_RANK = {"无": 0, "预警": 1, "异常": 2, "极度异常": 3}


def _to_float(v):
    try:
        s = str(v).replace(",", "").strip()
        if not s:
            return None
        if any(ch.isalpha() or ('\u4e00' <= ch <= '\u9fff') for ch in s):
            return None
        return float(s)
    except Exception:
        return None


# ---------------- 源文件一致性修复 ----------------
def reconcile_report(d):
    """负债合计与分项之和不一致时修正总负债（留痕返回说明列表）。
    修正优先级：① 分项之和能使 资产=负债+权益 成立 → 用分项之和；
    ② 否则尝试 资产-权益；③ 都无法验证 → 不修正，仅提示源文件内部不一致。"""
    notes = []
    bs = d.get("资产负债表", {})
    l = bs.get("总负债_期末")
    cl = bs.get("流动负债_期末")
    ncl = bs.get("非流动负债_期末")
    a = bs.get("总资产_期末")
    e = bs.get("所有者权益_期末")
    if None in (l, cl, ncl) or not l:
        return notes
    sub = cl + ncl
    if abs(sub - l) / abs(l) <= 0.001:
        return notes
    adopted = None
    if a is not None and e is not None:
        if abs((sub + e) - a) / abs(a) <= 0.001:
            adopted = sub
        else:
            alt = a - e
            if abs(alt - sub) / abs(sub) <= 0.001:
                adopted = alt
    if adopted is not None:
        bs["总负债_期末"] = adopted
        notes.append(f"源文件内部不一致：负债合计{l:,.2f}与流动负债+非流动负债{sub:,.2f}"
                     f"差额{abs(sub - l):,.2f}；已按修正值{adopted:,.2f}计算资产负债率/产权比率，"
                     f"源文件金额暂不可定稿")
    else:
        notes.append(f"源文件内部不一致：负债合计{l:,.2f}与分项之和{sub:,.2f}"
                     f"差额{abs(sub - l):,.2f}，且无法经资产=负债+权益验证，相关金额暂不可定稿")
    return notes


# ---------------- 单季口径推导 ----------------
def _cum_of(d):
    cum = {}
    for k, v in d.get("利润表", {}).items():
        if k.endswith("_本期"):
            cum[k[:-3]] = v
    for k, v in d.get("现金流量表", {}).items():
        cum[k] = v
    return cum


def derive_singles(companies, comp):
    """逐报告推导：cum(累计) / single(单季) / ring_single(上一报告单季) / py_single(上年同季单季)。
    单季口径仅在可用时给出：年内上一期累计可差分，或一季报单季=累计；
    年内首份非一季报报告（如只有年报）single=None，避免把全年误当 Q4。"""
    reports = companies.get(comp) or {}
    if not reports:
        return {}
    order = sorted(reports.items(),
                   key=lambda kv: (kv[1]["year"] or 0, PERIOD_ORDER.get(kv[1]["period"], 4)))
    by_yp = {(it["year"], it["period"]): fn for fn, it in order}
    out, prev_fn = {}, None
    for fname, item in order:
        cum = _cum_of(item["d"])
        if prev_fn is not None and (reports[prev_fn]["year"] or 0) == (item["year"] or 0):
            base = _cum_of(reports[prev_fn]["d"])
            single = {k: cum[k] - base.get(k, 0.0) for k in cum}
        elif item["period"] == "一季报":
            single = dict(cum)
        else:
            single = None   # 年内首份非一季报：缺少年内更早累计，单季不可拆
        py = by_yp.get(((item["year"] or 0) - 1, item["period"]))
        out[fname] = {
            "cum": cum,
            "single": single,
            "ring_single": (out[prev_fn]["single"] if prev_fn else None),
            "py_single": (out[py]["single"] if py else None),
        }
        prev_fn = fname
    return out


# ---------------- 指标计算 ----------------
def compute_indicators(d, single=None, ring_single=None, py_single=None):
    ind = {}
    if g(d, "资产负债表", "流动负债_期末"):
        if g(d, "资产负债表", "流动资产_期末") is not None:
            ind["流动比率"] = g(d, "资产负债表", "流动资产_期末") / g(d, "资产负债表", "流动负债_期末")
        ind["现金流量比率"] = g(d, "现金流量表", "经营活动现金净流量", 0) / g(d, "资产负债表", "流动负债_期末")
    if g(d, "资产负债表", "总资产_期末"):
        if g(d, "资产负债表", "总负债_期末") is not None:
            ind["资产负债率"] = g(d, "资产负债表", "总负债_期末") / g(d, "资产负债表", "总资产_期末")
            if g(d, "资产负债表", "所有者权益_期末"):
                ind["产权比率"] = g(d, "资产负债表", "总负债_期末") / g(d, "资产负债表", "所有者权益_期末")
                if g(d, "资产负债表", "所有者权益_期末") < 0:
                    ind["_产权比率_负权益"] = True

    for name, item, denom_key in [
        ("应收账款周转率同比", "应收账款", "营业收入"),
        ("存货周转率同比", "存货", "营业成本"),
        ("总资产周转率同比", "总资产", "营业收入")]:
        cur = g(d, "利润表", f"{denom_key}_本期"); pre = g(d, "利润表", f"{denom_key}_上期")
        e0 = g(d, "资产负债表", f"{item}_期末"); e1 = g(d, "资产负债表", f"{item}_期初")
        e2 = g(d, "资产负债表", f"{item}_上年初")
        if None not in (cur, pre, e0, e1, e2) and e1 and e2:
            cur_t = cur / ((e0 + e1) / 2); pre_t = pre / ((e1 + e2) / 2)
            if pre_t:
                ind[name] = cur_t / pre_t - 1
                if name == "总资产周转率同比":
                    ind["_总资产周转率"] = cur_t

    rev, rev_p = g(d, "利润表", "营业收入_本期"), g(d, "利润表", "营业收入_上期")
    if rev and rev_p:
        ind["营业收入增长率"] = rev / rev_p - 1
    cost, cost_p = g(d, "利润表", "营业成本_本期"), g(d, "利润表", "营业成本_上期")
    if None not in (rev, rev_p, cost, cost_p):
        ind["毛利率"] = (rev - cost) / rev
        ind["毛利率同比pp"] = ((rev - cost) / rev - (rev_p - cost_p) / rev_p) * 100
    op, op_p = g(d, "利润表", "营业利润_本期"), g(d, "利润表", "营业利润_上期")
    if None not in (rev, rev_p, op, op_p):
        ind["营业利润率同比pp"] = (op / rev - op_p / rev_p) * 100
    ni, ni_p = g(d, "利润表", "净利润_本期"), g(d, "利润表", "净利润_上期")
    if None not in (rev, rev_p, ni, ni_p):
        ind["净利率同比pp"] = (ni / rev - ni_p / rev_p) * 100
    if ni is not None and ni_p:
        ind["净利润增长率"] = (ni - ni_p) / abs(ni_p)

    if rev:
        ind["_营业收入_本期"] = rev
        if ni is not None:
            ind["_净利润_本期"] = ni
        ocf = g(d, "现金流量表", "经营活动现金净流量")
        if ocf is not None:
            ind["_经营现金流_本期"] = ocf

    ni_a = g(d, "利润表", "归属于母公司净利润_本期")
    eq_a_e = g(d, "资产负债表", "归母所有者权益_期末")
    eq_a_b = g(d, "资产负债表", "归母所有者权益_期初")
    if None not in (ni_a, eq_a_e, eq_a_b) and (eq_a_e + eq_a_b):
        ind["ROE"] = ni_a / ((eq_a_e + eq_a_b) / 2)
    else:
        ni_t = g(d, "利润表", "净利润_本期")
        e_e = g(d, "资产负债表", "所有者权益_期末")
        e_b = g(d, "资产负债表", "所有者权益_期初")
        if None not in (ni_t, e_e, e_b) and (e_e + e_b):
            ind["ROE"] = ni_t / ((e_e + e_b) / 2)

    if rev and g(d, "现金流量表", "经营活动现金净流量") is not None:
        ind["营业现金比率"] = g(d, "现金流量表", "经营活动现金净流量") / rev
    e_e = g(d, "资产负债表", "所有者权益_期末")
    e_b = g(d, "资产负债表", "所有者权益_期初")
    if None not in (e_e, e_b) and e_b:
        ind["资本积累率"] = (e_e - e_b) / e_b

    if single:
        srev = single.get("营业收入"); sni = single.get("净利润")
        if srev is not None and ring_single and ring_single.get("营业收入") and ring_single["营业收入"] > 0:
            ind["营业收入环比"] = srev / ring_single["营业收入"] - 1
        if sni is not None and ring_single and ring_single.get("净利润") and ring_single["净利润"] > 0:
            ind["净利润环比"] = sni / ring_single["净利润"] - 1
        if srev is not None and py_single and py_single.get("营业收入") and py_single["营业收入"] > 0:
            ind["营业收入单季同比"] = srev / py_single["营业收入"] - 1
        if sni is not None and py_single and py_single.get("净利润") and py_single["净利润"] > 0:
            ind["净利润单季同比"] = sni / py_single["净利润"] - 1
        ind["_单季营业收入"] = srev
        ind["_单季净利润"] = sni
    return ind


def _band(v, warn, abn, extreme, hb=True):
    if hb:
        if v >= warn: return None
        if v >= abn:  return ("预警", v)
        if v >= extreme: return ("异常", v)
        return ("极度异常", v)
    else:
        if v <= warn: return None
        if v <= abn:  return ("预警", v)
        if v <= extreme: return ("异常", v)
        return ("极度异常", v)


def check_indicators(ind):
    R = []
    for disp, key, warn, abn, extreme, hb, w, desc in THRESHOLDS:
        if key not in ind:
            continue
        if key == "产权比率" and ind.get("_产权比率_负权益"):
            R.append({"指标": disp, "数值": ind[key], "等级": "极度异常", "权重": w,
                      "说明": "所有者权益为负，资本结构失衡，产权比率不适用"})
            continue
        res = _band(ind[key], warn, abn, extreme, hb)
        if res:
            R.append({"指标": disp, "数值": res[1], "等级": res[0], "权重": w, "说明": desc})
    if "流动比率" in ind and ind["流动比率"] > 5:
        R.append({"指标": "流动比率", "数值": ind["流动比率"], "等级": "极度异常",
                  "权重": 5, "说明": "流动比率>5，资金闲置或异常"})
    return R


def check_gouji(d, ind, key_info=None):
    key_info = key_info or {}
    R = []
    rev = g(d, "利润表", "营业收入_本期")
    ni  = g(d, "利润表", "净利润_本期")

    def add(no, name, calc, base, tol, level, w, desc):
        if calc is None or base is None or base == 0:
            return
        diff = abs(calc - base) / abs(base)
        passed = diff <= tol
        R.append({
            "勾稽项名称": f"{no}.{name}",
            "理论计算值": round(calc, 2),
            "报表实际值": round(base, 2),
            "差异率": f"{diff * 100:.4f}%",
            "勾稽校验结论": "通过" if passed else "不通过",
            "等级": "通过" if passed else level,
            "权重": 0 if passed else w,
            "风险说明": "差异在容差范围内" if passed else desc,
        })

    a = g(d, "资产负债表", "总资产_期末")
    l = g(d, "资产负债表", "总负债_期末")
    e = g(d, "资产负债表", "所有者权益_期末")
    if None not in (a, l, e):
        add(1, "资产负债表平衡", l + e, a, 0.0001, "极高", 10, "会计恒等式不成立")

    cl = g(d, "资产负债表", "流动负债_期末")
    ncl = g(d, "资产负债表", "非流动负债_期末")
    if None not in (l, cl, ncl):
        add(1.1, "负债分项之和", cl + ncl, l, 0.0001, "极高", 10,
            "流动负债+非流动负债与总负债不一致，源文件可能存在印刷或提取错误")

    core = ["营业收入_本期", "营业成本_本期", "税金及附加", "销售费用",
            "管理费用", "研发费用", "财务费用", "营业利润_本期"]
    optionals = ["其他收益", "投资收益", "公允价值变动收益",
                 "信用减值损失", "资产减值损失", "资产处置收益"]
    vals = {k: g(d, "利润表", k) for k in core + optionals}
    if all(vals[k] is not None for k in core):
        assumed = [k for k in optionals if vals[k] is None]
        calc = (vals["营业收入_本期"] - vals["营业成本_本期"] - vals["税金及附加"]
                - vals["销售费用"] - vals["管理费用"] - vals["研发费用"]
                - vals["财务费用"] + sum(vals[k] or 0.0 for k in optionals))
        note = "可能存在利润操纵、科目分类错误或勾稽口径缺项"
        if assumed:
            note += f"（以下科目按0处理：{'、'.join(assumed)}，若金额较大将造成误报）"
        add(2, "营业利润勾稽", calc, vals["营业利润_本期"], 0.005, "高", 8, note)

    tp = g(d, "利润表", "利润总额_本期")
    tax = g(d, "利润表", "所得税费用_本期")
    if None not in (ni, tp, tax):
        add(3, "净利润勾稽", tp - tax, ni, 0.001, "高", 8, "所得税或利润总额错误")

    ce = g(d, "现金流量表", "现金及现金等价物_期末")
    cb = g(d, "现金流量表", "现金及现金等价物_期初")
    cn = g(d, "现金流量表", "现金及现金等价物净增加额")
    if None not in (ce, cb, cn):
        add(4, "现金净增加勾稽", ce - cb, cn, 0.001, "高", 8, "三大活动分类错误")

    re_e = g(d, "资产负债表", "未分配利润_期末")
    re_b = g(d, "资产负债表", "未分配利润_期初")
    ni_attrib = _to_float(key_info.get("归母净利润", "")) if key_info else None
    if ni_attrib is None:
        ni_attrib = g(d, "利润表", "归属于母公司净利润_本期")
    if ni_attrib is None:
        ni_attrib = ni
    div = _to_float(key_info.get("现金分红_普通股_金额", "")) if key_info else None
    if div is None:
        div = _to_float(key_info.get("现金分红_普通股", "")) if key_info else None
    if div is None:
        div_fallback = g(d, "现金流量表", "分红付息现金")
        if div_fallback is not None:
            div = div_fallback
            tol = 0.05
        else:
            tol = 0.01
    else:
        tol = 0.01
    sur_e = g(d, "资产负债表", "盈余公积_期末")
    sur_b = g(d, "资产负债表", "盈余公积_期初")
    appropriation = (sur_e - sur_b) if None not in (sur_e, sur_b) else 0.0
    if None not in (re_e, re_b, div, ni_attrib):
        calc = re_b + ni_attrib - div - appropriation
        note = ("未扣除项可能含盈余公积提取外的其他留存收益变动：其他综合收益结转、"
                "永续债/其他权益工具利息、股份支付计入、前期差错更正")
        if appropriation:
            note = f"已扣除盈余公积提取{appropriation:,.2f}元；" + note
        add(5, "未分配利润勾稽", calc, re_e, tol, "高", 8, note)

    scash = g(d, "现金流量表", "销售商品提供劳务收到的现金")
    if scash is not None and rev:
        expect = rev * (1 + VAT_RATE)
        diff = abs(expect - scash) / abs(expect)
        passed = diff <= 0.15
        R.append({
            "勾稽项名称": "7.营收现金含量(含销项税口径)",
            "理论计算值": round(expect, 2),
            "报表实际值": round(scash, 2),
            "差异率": f"{diff * 100:.4f}%",
            "勾稽校验结论": "通过" if passed else "不通过",
            "等级": "通过" if passed else "高",
            "权重": 0 if passed else 8,
            "风险说明": ("差异在容差范围内" if passed else
                      "可能存在虚增收入、提前确认收入或大额应收挂账（已按13%销项税调整口径）"),
        })

    pcash = g(d, "现金流量表", "购买商品接受劳务支付的现金")
    cost = g(d, "利润表", "营业成本_本期")
    if pcash is not None and cost:
        add(8, "成本现金支付", cost, pcash, 0.25, "中", 6, "需核查存货变动及应付账款变化")

    re_b_prev = g(d, "资产负债表", "未分配利润_上年末")
    if re_b is not None and re_b_prev is not None:
        diff = abs(re_b - re_b_prev) / abs(re_b_prev) if re_b_prev else 0
        passed = diff <= 0.005
        R.append({
            "勾稽项名称": "11.连续期勾稽",
            "理论计算值": round(re_b_prev, 2),
            "报表实际值": round(re_b, 2),
            "差异率": f"{diff * 100:.4f}%",
            "勾稽校验结论": "通过" if passed else "不通过",
            "等级": "通过" if passed else "极高",
            "权重": 0 if passed else 10,
            "风险说明": "期初与上年同期期末衔接一致" if passed else "期初期末不一致，可能存在篡改、追溯调整未披露",
        })
    return R