from config import (RING_CHANGE_LIMIT, CASH_PROFIT_RATIO,
                    NONREC_RATIO, NONREC_HIGH_RATIO)


def _key_info_value(key_info, key, default=""):
    if not key_info:
        return default
    v = key_info.get(key, default)
    return v if v is not None else default


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


def _audit_level(audit: str):
    if not audit:
        return None
    if "无法表示意见" in audit:
        return ("极高", 15, "审计机构对财务报表出具无法表示意见，报表可靠性严重存疑")
    if "否定意见" in audit:
        return ("极高", 15, "审计机构否定意见，报表未公允反映财务状况")
    if "带强调事项段" in audit:
        return ("高风险", 10, "审计意见带强调事项段，存在需提请注意的重大事项")
    if "无保留" in audit:
        return None
    if "保留意见" in audit:
        return ("高风险", 10, "审计机构保留意见，存在重大不确定性")
    return None


def ring_from_singles(single_info: dict) -> dict:
    """由 derive_singles 的单季口径计算环比（分母<=0 不计算）。"""
    ring = {}
    if not single_info or not single_info.get("single"):
        return ring
    rs = single_info.get("ring_single")
    if not rs:
        return ring
    for k, label in [("营业收入", "营业收入单季环比"), ("净利润", "净利润单季环比")]:
        a = single_info["single"].get(k)
        b = rs.get(k)
        if a is not None and b and b > 0:
            ring[label] = a / b - 1
    return ring


def scan(d, ind, gouji, continuity, nonrec_df, ring, key_info=None,
         reconcile_notes=None):
    A = []
    key_info = key_info or {}

    # --- 0) 源文件内部一致性（最高优先级，金额暂不可定稿） ---
    for note in (reconcile_notes or []):
        A.append({"类别": "源文件一致性", "项目": "负债合计与分项之和不一致",
                  "数值": "", "等级": "极高", "权重": 10, "说明": note})

    # --- 0.1) 勾稽不通过项转告警 ---
    for r in gouji:
        if r.get("勾稽校验结论") != "不通过":
            continue
        A.append({"类别": "勾稽校验", "项目": r["勾稽项名称"], "数值": r.get("差异率", ""),
                  "等级": r["等级"], "权重": r["权重"],
                  "说明": f"理论值{r.get('理论计算值')} vs 实际值{r.get('报表实际值')}，{r['风险说明']}"})

    # --- 1) 指标阈值 ---
    for r in ind.get("_threshold_hits", []):
        v = r["数值"]
        if isinstance(v, float):
            note = f"{r['说明']}（同比变动 {v:.4f}pp）" if "pp" in r["说明"] else f"{r['说明']}（当前值 {v:.4f}）"
        else:
            note = r["说明"]
        A.append({"类别": "指标阈值", "项目": r["指标"], "数值": round(v, 4) if isinstance(v, float) else v,
                  "等级": r["等级"], "权重": r["权重"], "说明": note})

    # --- 2) 审计意见非标 ---
    audit = str(_key_info_value(key_info, "审计意见类型", "")).strip()
    hit = _audit_level(audit)
    if hit:
        A.append({"类别": "审计意见", "项目": audit, "数值": audit,
                  "等级": hit[0], "权重": hit[1], "说明": hit[2]})

    # --- 3) 退市风险：净资产为负 ---
    e_e = d.get("资产负债表", {}).get("所有者权益_期末")
    if e_e is not None and e_e < 0:
        A.append({"类别": "退市风险", "项目": "净资产为负", "数值": f"{e_e:,.2f}",
                  "等级": "极高", "权重": 15,
                  "说明": "期末净资产为负，触及退市风险警示"})

    # --- 4) 持续经营迹象 ---
    if e_e is not None and e_e < 0 and (ind.get("现金流量比率") or 0) < 0:
        A.append({"类别": "持续经营", "项目": "资不抵债+经营现金流为负", "数值": "存在重大不确定性",
                  "等级": "高风险", "权重": 12,
                  "说明": "所有者权益为负且经营现金流净流出，持续经营能力存在重大不确定性"})

    # --- 5) 利润与现金流背离 ---
    ni = ind.get("_净利润_本期"); ocf = ind.get("_经营现金流_本期")
    if ni is not None and ocf is not None:
        if ni > 0 and ocf < 0:
            A.append({"类别": "利润现金流背离", "项目": "净现比背离",
                      "数值": f"净利润{ni:,.0f}/经营现金流{ocf:,.0f}",
                      "等级": "高风险", "权重": 10,
                      "说明": "盈利但经营现金流为负，纸面利润，需核查应收/存货挂账与收入确认政策"})
        elif ni > 0 and ocf / ni < CASH_PROFIT_RATIO:
            A.append({"类别": "利润现金流背离", "项目": "净现比偏低",
                      "数值": round(ocf / ni, 3), "等级": "预警", "权重": 7,
                      "说明": f"经营现金流/净利润={ocf/ni:.2f}，低于{CASH_PROFIT_RATIO}，盈利质量存疑"})

    # --- 6) 非经常性损益 ---
    ni_attrib = _to_float(_key_info_value(key_info, "归母净利润", ""))
    kf = _to_float(_key_info_value(key_info, "扣非归母净利润", ""))
    nonrec_total = _to_float(_key_info_value(key_info, "非经常性损益合计", ""))
    if nonrec_total is None and nonrec_df is not None and len(nonrec_df):
        try:
            nonrec_total = float(nonrec_df["本期金额"].sum())
        except Exception:
            nonrec_total = None
    ni_for_nonrec = ni_attrib if ni_attrib is not None else ni

    if kf is not None and ni_attrib is not None and ni_attrib > 0 and kf < 0:
        A.append({"类别": "非经常性损益", "项目": "扣非亏损、账面盈利",
                  "数值": f"归母{ni_attrib:,.2f}/扣非{kf:,.2f}",
                  "等级": "高风险", "权重": 10,
                  "说明": "扣非归母净利润为负而归母净利润为正，主业实际亏损、盈利完全依赖非经常性损益"})

    nonrec_reliable = True
    if nonrec_total is not None and ni_for_nonrec and ni_for_nonrec > 0:
        if abs(nonrec_total) > ni_for_nonrec * 1.5:
            nonrec_reliable = False
    if nonrec_total is not None and nonrec_reliable and ni_for_nonrec and ni_for_nonrec > 0:
        ratio = abs(nonrec_total) / ni_for_nonrec
        if ratio >= NONREC_HIGH_RATIO:
            A.append({"类别": "非经常性损益", "项目": "非经常性损益占归母净利润比例",
                      "数值": round(ratio, 4), "等级": "异常", "权重": 8,
                      "说明": f"非经常性损益合计{nonrec_total:,.2f}元，占归母净利润{ratio:.2%}"
                              f"（≥{NONREC_HIGH_RATIO:.0%}），利润高度依赖一次性项目"})
        elif ratio >= NONREC_RATIO:
            A.append({"类别": "非经常性损益", "项目": "非经常性损益占归母净利润比例",
                      "数值": round(ratio, 4), "等级": "预警", "权重": 5,
                      "说明": f"非经常性损益合计{nonrec_total:,.2f}元，占归母净利润{ratio:.2%}"
                              f"（≥{NONREC_RATIO:.0%}），需以扣非口径评估主业真实盈利"})

    # --- 7) 会计口径变化 ---
    for c in continuity:
        A.append({"类别": "会计口径变化", "项目": f"期初衔接:{c['科目']}", "数值": c["差异%"],
                  "等级": "高", "权重": 9,
                  "说明": f"{c['当前报告']}期初数与{c['上一报告']}期末数不一致，"
                          f"提示追溯调整/重述/合并范围变化，需核对附注"})

    # --- 8) 环比骤变（单季口径） ---
    for k, v in (ring or {}).items():
        if v is None:
            continue
        if abs(v) > RING_CHANGE_LIMIT:
            A.append({"类别": "环比骤变", "项目": k, "数值": round(v, 4), "等级": "预警", "权重": 6,
                      "说明": f"单季环比变动超{RING_CHANGE_LIMIT:.0%}，注意季节性、单季确认集中或口径变化"})

    # --- 9) 趋势高压线 ---
    if ind.get("营业收入增长率") is not None and ind["营业收入增长率"] < -0.5:
        A.append({"类别": "趋势高压线", "项目": "营收同比下降>50%",
                  "数值": round(ind["营业收入增长率"], 4),
                  "等级": "高风险", "权重": 10, "说明": "营收同比下降超50%，判定为高风险异常"})
    if ind.get("净利润增长率") is not None and ind["净利润增长率"] < -0.5:
        A.append({"类别": "趋势高压线", "项目": "净利润同比下降>50%",
                  "数值": round(ind["净利润增长率"], 4),
                  "等级": "高风险", "权重": 10, "说明": "净利润同比下降超50%，判定为高风险异常"})

    # --- 10) 纸面利润 ---
    rev_g = ind.get("营业收入增长率")
    for item, label, w in [("应收账款", "应收账款", 8), ("存货", "存货", 6)]:
        e0 = d.get("资产负债表", {}).get(f"{item}_期末")
        e1 = d.get("资产负债表", {}).get(f"{item}_期初")
        if None not in (e0, e1, rev_g) and e1 > 0 and e0 > e1:
            gap = (e0 / e1 - 1) - rev_g
            if gap > 0.3 and (e0 / e1 - 1) > 0.2:
                tail = ("需核查坏账准备计提与期后回款" if item == "应收账款"
                        else "需核查跌价准备计提与库龄结构")
                A.append({"类别": "纸面利润", "项目": f"{label}增速远超营收增速",
                          "数值": round(gap, 4), "等级": "预警", "权重": w,
                          "说明": f"{label}同比{e0/e1 - 1:.2%} vs 营收同比{rev_g:.2%}，"
                                  f"增速差{gap:.2%}，收入含金量下降，{tail}"})

    return A


def compute_ring(cur_d, prior_d):
    """【旧接口，已弃用】新流程请使用 ring_from_singles()（单季口径）。"""
    ring = {}
    if not prior_d:
        return ring
    for std, label in [("营业收入_本期", "营业收入环比"), ("净利润_本期", "净利润环比")]:
        a = cur_d.get("利润表", {}).get(std)
        b = prior_d.get("利润表", {}).get(std)
        if a is not None and b:
            ring[label] = a / b - 1
    return ring