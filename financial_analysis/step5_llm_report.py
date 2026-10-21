from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

SYSTEM = """你是一名任职于头部会计师事务所的资深财务鉴证专家，为上市公司出具专业尽调分析。请严格遵守以下要求：

【输出范围】只输出『专项归因分析』章节的Markdown正文，不要输出报告大标题，不要重复其他章节内容。

【章节结构】按以下固定顺序组织：
一、总体评价：3句话以内，概括本期财务状况的核心特征与最值得关注的1-2个问题。
二、逐项异常归因：对给定的每一条异常，按以下模板输出独立小节：
   ### 异常N：<异常项目名称>
   - **【事实】**：仅陈述上下文中可验证的数据（必须带具体金额/数值/百分比）。
   - **【逻辑链】**：从事实到结论的完整推理链条，用箭头串联每一步。
   - **【推测】**：给出最可能的业务/会计原因，按可能性排序，标注置信度：高/中/低。
   - **【核查路径】**：给出2-3条具体可执行的核查动作。
三、专项讨论：依次讨论以下三个主题，每个主题同样遵循【事实】【逻辑链】【推测】【核查路径】：
   1) 非经常性损益：是否存在用非经常性损益掩盖主业下滑；
   2) 会计口径变化：是否存在追溯调整、重述、合并范围变化；
   3) 利润与现金流背离：净现比、收现率是否健康，纸面利润风险。
   上下文已提供的字段必须直接引用，不得再说"数据不足"。
四、结论与建议：3条以内，按重要性排序。

【口径与计算规范】
1. 非经常性损益占归母净利润的比例，必须用上下文『非经常性损益合计』字段计算，
   严禁将明细行简单加总（合计行已含所得税影响、少数股东权益影响等减项）；
2. 净现比（经营现金流/净利润）衡量利润含金量；现金流量比率（经营现金流/流动负债）
   衡量短期债务覆盖。两者口径不同，可能一高一低并存，必须分别解释、不得混同；
3. 经行业特性豁免的异常，归因重点为资金使用效率，除现金收益评价外，
   还应提示核查货币资金受限金额、募集资金专户及理财配置情况；
4. 若关键辅助信息与异常清单表述冲突，以关键辅助信息为准，并在归因中指明冲突。
5. 普通股分红只能使用上下文『现金分红_普通股』字段，严禁将『分红付息现金』
   或现金流量表复合项目直接当作普通股分红计算比例。
6. 净利润分母必须使用上下文『净利润_本期』或『归母净利润』字段，
   严禁用未分配利润变动、权益变动等替代。
7. 若源文件存在内部冲突（如资产负债表不平衡、负债合计与分项不符），
   必须在报告中明确写"源文件内部不一致，相关金额暂不可定稿"，
   不得强行给出确定结论或归因；
8. 若异常清单含『源文件一致性』类别，相关科目金额一律视为暂不可定稿，
   涉及该科目的指标按修正值列示并注明修正过程。

【格式规范】
1. 小节标题用三级标题"### "+中文序号，禁止使用一级、二级标题；
2. 列表用"- "，异常归因用"### 异常N："编号；
3. 表格：表头一行、分隔行一行（|---|），列数全文一致；
4. 数字：金额统一千分位+两位小数，比率/百分比统一4位小数；单位为元；
5. 禁止HTML标签、代码块、引用块；加粗仅用于【事实】【逻辑链】【推测】【核查路径】及关键结论；
6. 全文中文标点，语言克制、审慎。

【硬性禁令】
1. 禁止编造上下文中不存在的数字；
2. 【事实】段只准引用上下文数据；推测放【推测】段并标注置信度；
3. 已被行业特性豁免的异常，在归因时注明"经行业特性豁免"。
4. 禁止把投资活动/筹资活动现金支出说成"消耗经营现金流"。
5. 禁止在数据缺失时强行计算比率或用其他科目替代。
用中文输出。"""

_KEEP_KEYS = ("营业收入_本期", "营业收入_上期", "营业成本_本期", "营业成本_上期",
              "营业利润_本期", "利润总额_本期", "净利润_本期", "净利润_上期",
              "所得税费用_本期", "经营活动现金净流量", "销售商品提供劳务收到的现金",
              "购买商品接受劳务支付的现金", "分红付息现金",
              "总资产_期末", "总负债_期末", "所有者权益_期末", "所有者权益_期初",
              "流动资产_期末", "流动负债_期末", "未分配利润_期末", "未分配利润_期初")


def _fmt(v):
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def _fmt_num(v):
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def _ind_label(k: str) -> str:
    """核心指标显示名：同比pp类标注单位，避免被误读为当前值"""
    return f"{k}（百分点）" if k.endswith("pp") else k


def _build_llm_context(company, period, ind, alerts, gouji, evid, nonrec_df, d,
                       key_info=None, meta=None) -> str:
    key_info = key_info or {}
    lines = [f"公司：{company}", f"报告期：{period}", ""]

    # 数据质量声明
    missing = []
    for k in ["审计意见类型", "归母净利润", "扣非归母净利润", "非经常性损益合计",
              "现金分红_普通股", "分红付息现金"]:
        if not key_info.get(k):
            missing.append(k)
    if missing:
        lines.append(f"## ⚠️ 数据质量声明：以下关键字段未提取到：{', '.join(missing)}")
        lines.append("请基于现有数据审慎分析，缺失字段不得强行推断或替代。")
        lines.append("")

    lines += ["## 〇、关键辅助信息（已提供，请直接引用，不要再说数据不足）",
              "注：本上下文所有金额均已统一换算为元（原报表可能以千元/万元列示）。"]
    for k in ["审计意见类型", "所属行业", "归母净利润", "扣非归母净利润",
              "非经常性损益合计", "现金分红_普通股", "现金分红_普通股_金额",
              "分红付息现金", "总股本", "合并范围变化"]:
        v = key_info.get(k, "")
        if v:
            lines.append(f"- {k}: {v}")
    for k in ["归母净利润_备注", "扣非归母净利润_备注"]:
        v = key_info.get(k, "")
        if v:
            lines.append(f"- {k}: {v}")
    if meta and meta.get("所属行业"):
        lines.append(f"- 所属行业（规范化，行业豁免判定依据）: {meta['所属行业']}")
    nr = key_info.get("非经常性损益明细") or []
    if nr:
        lines.append("- 非经常性损益明细（原始行，仅供展示；占比计算必须用『非经常性损益合计』）:")
        for item in nr[:30]:
            lines.append(f"  - {item}")
    lines.append("")

    lines += ["## 一、核心指标（含同比）"]
    for k, v in ind.items():
        if not k.startswith("_"):
            lines.append(f"- {_ind_label(k)}: {v:.4f}" if isinstance(v, float) else f"- {_ind_label(k)}: {v}")

    lines += ["", "## 二、三张报表全部标准化科目（单位：元）"]
    _STMT_TITLE = {"资产负债表": "合并资产负债表（期末/期初）",
                   "利润表": "合并利润表（本期/上期）",
                   "现金流量表": "合并现金流量表（本期）"}
    for stmt, items in d.items():
        if not items:
            continue
        lines += ["", f"### {_STMT_TITLE.get(stmt, stmt)}"]
        for k, v in items.items():
            tag = " ←关键科目" if k in _KEEP_KEYS else ""
            lines.append(f"- {k}: {_fmt_num(v)}{tag}")

    lines += ["", "## 三、派生核心比率（口径提示：净现比衡量利润含金量，"
              "现金流量比率衡量对流动负债覆盖，两者可背离，须分别解释）"]
    ni = d.get("利润表", {}).get("净利润_本期")
    ocf = d.get("现金流量表", {}).get("经营活动现金净流量")
    rev = d.get("利润表", {}).get("营业收入_本期")
    scash = d.get("现金流量表", {}).get("销售商品提供劳务收到的现金")
    pcash = d.get("现金流量表", {}).get("购买商品接受劳务支付的现金")
    cost = d.get("利润表", {}).get("营业成本_本期")
    if ni and ocf is not None:
        lines.append(f"- 净现比（经营现金流/净利润）: {ocf / ni:.4f}")
    if rev and scash is not None:
        lines.append(f"- 收现率（销售收现/营业收入）: {scash / rev:.4f}")
    if rev and ni:
        lines.append(f"- 净利率: {ni / rev:.4f}")
    if cost and pcash is not None:
        lines.append(f"- 付现率（购买商品付现/营业成本）: {pcash / cost:.4f}")
    if rev and ocf is not None:
        lines.append(f"- 营业现金比率（经营现金流/营业收入）: {ocf / rev:.4f}")

    lines += ["", "## 四、勾稽校验明细"]
    for r in gouji:
        lines.append(f"- {r['勾稽项名称']}：理论值 {_fmt_num(r['理论计算值'])} vs "
                     f"实际值 {_fmt_num(r['报表实际值'])}，差异率 {r['差异率']}，"
                     f"结论【{r['勾稽校验结论']}】（{r.get('风险说明', '')}）")

    lines += ["", "## 五、异常清单（需逐项归因）"]
    if alerts:
        for i, a in enumerate(sorted(alerts, key=lambda x: -x.get("权重", 0)), 1):
            ex = "（已行业豁免：归因重点为资金使用效率评价）" if a.get("行业豁免") else ""
            lines.append(f"{i}. [{a['等级']}] {a['类别']}｜{a['项目']} = {a['数值']}｜"
                         f"{a['说明']}{ex}")
    else:
        lines.append("（系统未触发阈值告警，请结合指标与原始金额自行判断潜在关注项）")

    lines += ["", "## 六、科目证据位置（PDF来源）"]
    for k, pos in list(evid.items())[:60]:
        lines.append(f"- {k}: {pos}")

    if nonrec_df is not None and len(nonrec_df):
        lines += ["", "## 七、非经常性损益相关科目（附注提取，仅供展示）"]
        for _, r in nonrec_df.iterrows():
            lines.append(f"- {r['项目']}: {r['本期金额']}（{r['数据位置']}）")
    return "\n".join(lines)


def _call_llm(ctx, enable_thinking) -> str:
    """单次LLM调用，返回content（可能为空串）"""
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": ctx}],
        stream=False,
        temperature=0.3,
        extra_body={"thinking": {"type": "enabled" if enable_thinking else "disabled"}})
    return resp.choices[0].message.content or ""


def _llm_discussion(ctx) -> str:
    try:
        out = _call_llm(ctx, enable_thinking=True)
        if out.strip():
            return out
        print("[step5] ⚠️ 思考模式返回空内容，降级为关闭thinking重试")
    except Exception as exc:
        print(f"[step5] ⚠️ 思考模式调用失败（{exc}），降级重试")
    try:
        out = _call_llm(ctx, enable_thinking=False)
        if out.strip():
            return out
        print("[step5] ⚠️ 降级重试仍返回空内容")
    except Exception as exc:
        print(f"[step5] ⚠️ 降级重试调用失败（{exc}）")
    return "> ⚠️ LLM归因分析生成失败（返回空内容或调用异常），以下为规则版归因要点，请人工复核。\n\n" + ctx


def generate(company, period, ind, alerts, gouji, evid, nonrec_df, d, meta, risk,
             paths, exempt_log=None, key_info=None) -> str:
    level, risk_desc = risk
    code = meta.get("股票代码") or "未配置"
    industry = meta.get("所属行业") or "未配置"

    parts = ["# 上市公司财务报告分析\n",
             f"## {company}（股票代码：{code}｜所属行业：{industry}）",
             f"**报告期：{period}**\n"]

    parts.append("\n## 一、异常关注点\n")
    if alerts:
        parts.append("| 序号 | 异常项目 | 异常等级 | 行业豁免 | 异常判定理由 |")
        parts.append("|---:|---|---|---|---|")
        for i, a in enumerate(sorted(alerts, key=lambda x: -x.get("权重", 0)), 1):
            tag = a.get("行业豁免", "")
            level_txt = f"**{a['等级']}**" + ("（行业豁免）" if tag else "")
            parts.append(f"| {i} | {a['类别']}｜{a['项目']} | {level_txt} | {tag} | {a['说明']} |")
    else:
        parts.append("系统未触发阈值类告警。不代表无风险，请结合下文指标与专项讨论人工复核。")

    parts.append("\n## 二、勾稽校验表\n")
    parts.append("| 勾稽项名称 | 理论计算值 | 报表实际值 | 差异率 | 勾稽校验结论 |")
    parts.append("|---|---:|---:|---:|---|")
    for r in gouji:
        parts.append(f"| {r['勾稽项名称']} | {_fmt(r['理论计算值'])} | {_fmt(r['报表实际值'])} "
                     f"| {r['差异率']} | **{r['勾稽校验结论']}** |")
    n_pass = sum(1 for r in gouji if r["勾稽校验结论"] == "通过")
    parts.append(f"\n> 勾稽校验：共执行 {len(gouji)} 项，通过 {n_pass} 项，"
                 f"不通过 {len(gouji) - n_pass} 项。\n")

    parts.append("\n## 三、核心指标\n")
    parts.append("| 指标 | 数值 |")
    parts.append("|---|---:|")
    for k, v in sorted(ind.items()):
        if k.startswith("_"):
            continue
        parts.append(f"| {_ind_label(k)} | {v:.4f} |" if isinstance(v, float) else f"| {_ind_label(k)} | {v} |")

    parts.append("\n## 四、整体风险等级\n")
    parts.append(f"### **{level}**\n")
    parts.append(f"{risk_desc}\n")

    parts.append("\n## 五、行业特性豁免说明\n")
    if exempt_log:
        parts.append("| 项目 | 原等级 | 调整后 | 豁免理由 |")
        parts.append("|---|---|---|---|")
        for e in exempt_log:
            parts.append(f"| {e['项目']} | {e['原始等级']} | 预警 | {e['豁免理由']} |")
        parts.append("\n> 豁免项仅代表该异常主要由行业经营特性所致、不构成重大风险信号，"
                     "记录仍保留以供追溯；判定依据见 mapping/industry_profile.csv。\n")
    else:
        parts.append("本报告期无适用行业豁免的异常项。")

    parts.append("\n## 六、专项归因分析\n")
    ctx = _build_llm_context(company, period, ind, alerts, gouji, evid, nonrec_df, d,
                             key_info=key_info, meta=meta)
    parts.append(_llm_discussion(ctx))

    report = "\n".join(parts)
    paths.REPORT_MD.write_text(report, encoding="utf-8")
    print(f"[step5] 报告已生成 → output/{paths.report_name}/{paths.REPORT_MD.name}")
    return report