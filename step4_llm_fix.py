import pandas as pd
from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

SYSTEM = """你是证券研究报告合规核查专家。依据上下文（基准财务数据+规则核查结果），
对每条『错误/待复核』声明输出核查意见。严格规范：
1. 【结论复核】确认或否决规则结论；否决须给出依据（视为误报，明确写出）。
2. 【来源定位】给出基准数值的精确出处（报表·科目 or 关键信息字段）。
3. 【修改建议】必须给出可直接替换原文的文本（含修正后的数字/口径/单位），禁止"建议核实"类模糊措辞；数字用千分位+两位小数。
4. 【引用溯源】对引用类声明：核对文件名/年份/机构是否可信，疑似的错年份/虚构引用须指明并给出应替换的准确表述；上下文无法验证的写"需人工溯源"，不得臆断。
5. 禁止编造上下文中不存在的数字；推测必须标注"推测"。
只输出Markdown，每条声明一个小节：### 声明N（行号X）：[结论] 一句话，随后列 - 修改建议：… - 来源：… 。"""

def _fmt(v):
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return str(v)

def _build_context(rows: list[dict], cites: list[dict], base_ctx: str) -> str:
    L = [base_ctx, "", "## 待核查声明（规则引擎结果）"]
    for i, r in enumerate(rows, 1):
        L.append(f"{i}. 行{r['行号']}｜{r['声明类型']}｜{r['基准键']}｜"
                 f"原文：{r['原文'][:120]}")
        L.append(f"   草稿值={_fmt(r.get('草稿数值_元'))}；基准值={_fmt(r.get('基准数值_元'))}；"
                 f"来源={r.get('基准来源','')}；规则结论={r['核查结论']}"
                 f"{('｜' + r['错误类型']) if r.get('错误类型') else ''}｜{r.get('核查说明','')}")
    if cites:
        L += ["", "## 引用类声明（需溯源）"]
        for i, c in enumerate(cites, 1):
            L.append(f"{i}. 行{c['行号']}：{c['引用原文']}")
    return "\n".join(L)

def _call(ctx, thinking: bool) -> str:
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": ctx}],
        stream=False, temperature=0.2,
        extra_body={"thinking": {"type": "enabled" if thinking else "disabled"}})
    return resp.choices[0].message.content or ""

def generate_fixes(rows: list[dict], cites: list[dict], base_ctx: str, log=print) -> str:
    if not rows and not cites:
        return "本次核查未发现错误或待复核项，无需修改建议。"
    ctx = _build_context(rows, cites, base_ctx)
    for think in (True, False):
        try:
            out = _call(ctx, think)
            if out.strip():
                return out
            log(f"[step4] ⚠️ thinking={think} 返回空内容，降级重试")
        except Exception as exc:
            log(f"[step4] ⚠️ LLM调用失败（{exc}），降级重试")
    log("[step4] LLM不可用，回退规则版修改建议")
    L = ["## 规则版修改建议（LLM不可用，请人工复核）"]
    for i, r in enumerate(rows, 1):
        if r["核查结论"] == "错误" and r.get("基准数值_元") is not None:
            L.append(f"### 声明{i}（行{r['行号']}）：【{r['错误类型']}·{r['等级']}】{r.get('核查说明','')}")
            L.append(f"- 修改建议：将『{r['基准键']}』改为基准值 {_fmt(r['基准数值_元'])} 元"
                     f"（来源：{r.get('基准来源','')}）")
    for i, c in enumerate(cites, 1):
        L.append(f"### 引用{i}（行{c['行号']}）：【待人工溯源】{c['引用原文']}")
        L.append("- 修改建议：请核对文件名称、发文年份与机构后替换为准确表述")
    return "\n".join(L)
