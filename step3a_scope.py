from __future__ import annotations

from config import INDICATORS_8, EXTRA_INDICATORS


def classify_claim(c: dict) -> dict:
    """输入一条声明（ind_claims 输出字段），返回 {类别, 跳过原因, 备注, 报告期展示}。"""
    ind = str(c.get("指标", ""))
    flags = c.get("标记", "") or ""
    period = str(c.get("报告期", ""))
    year = c.get("年份")
    out = {"类别": "参与比对", "跳过原因": "", "备注": "", "报告期展示": period}

    if ind not in INDICATORS_8 and ind not in EXTRA_INDICATORS:
        out.update({"类别": "跳过", "跳过原因": "不在8项指标清单内（规则§三④）"})
        return out
    if "预测" in flags:
        out.update({"类别": "跳过",
                    "跳过原因": "未来业绩预测（前瞻数据，规则§三①）"})
        return out
    if "单季度" in flags:
        out.update({"类别": "跳过",
                    "跳过原因": "单季度口径（规则§三②）",
                    "报告期展示": f"{year}年一季度（单季）" if year else "单季"})
        return out
    if "TTM" in flags:
        out.update({"类别": "跳过", "跳过原因": "TTM滚动口径（规则§三②）"})
        return out
    if "估算" in flags:
        out.update({"类别": "跳过", "跳过原因": "估算值（规则§三③）"})
        return out
    # 口径差异：归母口径 vs 权益合计口径（规则§三示例：归母净资产增长率 vs 资本积累率）
    if ind == "资本积累率" and "归母" in flags:
        out.update({"类别": "口径差异",
                    "跳过原因": "归母口径 vs 基准库权益合计口径（规则§三②口径差异示例）",
                    "备注": "仅留痕，不计入错误数量统计"})
        return out
    if not period.endswith("年年报"):
        out.update({"类别": "跳过",
                    "跳过原因": f"非年报口径（报告期『{period}』，规则§三②）"})
        return out
    return out


def scope_claims(claims: list[dict]) -> list[dict]:
    """为每条声明附加范围判定字段，返回新列表（不修改原字典）。"""
    out = []
    for c in claims:
        r = dict(c)
        r.update(classify_claim(c))
        out.append(r)
    return out
