import csv
import json
from pathlib import Path
import pandas as pd

from step3_indicators import THRESHOLDS, IND_CLASS, SEVERITY_RANK

COMPANY_META_CSV = Path(__file__).resolve().parent / "mapping" / "company_meta.csv"
INDUSTRY_PROFILE_CSV = Path(__file__).resolve().parent / "mapping" / "industry_profile.csv"

_HIGH_WORDS = ("高于", "超过", "大于", ">")
_LOW_WORDS  = ("低于", "小于", "<")


def _load_industry_rules():
    if not INDUSTRY_PROFILE_CSV.exists():
        return []
    try:
        df = pd.read_csv(INDUSTRY_PROFILE_CSV, dtype=str).fillna("")
        return df.to_dict("records")
    except Exception:
        return []


def apply_industry_exemption(alerts: list, industry: str) -> tuple:
    if not alerts or not industry:
        return alerts, []
    rules = _load_industry_rules()
    adjusted, exempt_log = [], []
    for a in alerts:
        hit_rule = None
        for r in rules:
            if r.get("行业关键词", "").strip() not in industry:
                continue
            if r.get("指标", "").strip() not in str(a.get("项目", "")):
                continue
            direction = r.get("方向", "").strip()
            if direction and direction not in ("过高", "过低"):
                continue
            if direction:
                words = _HIGH_WORDS if direction == "过高" else _LOW_WORDS
                if not any(w in str(a.get("说明", "")) for w in words):
                    continue
            hit_rule = r
            break
        if hit_rule is None:
            adjusted.append(a)
            continue
        b = dict(a)
        b["原始等级"] = a.get("等级", "")
        b["原始权重"] = a.get("权重", 0)
        b["行业豁免"] = "是"
        b["等级"] = "预警"
        b["权重"] = 0          # 豁免后不计分，避免推高整体风险等级
        b["说明"] = (f"{a.get('说明', '')}｜【行业豁免】{hit_rule.get('豁免理由', '')}，"
                     f"等级由【{b['原始等级']}】调整为预警，不再计入风险得分")
        adjusted.append(b)
        exempt_log.append({"项目": b["项目"], "原始等级": b["原始等级"],
                           "原始权重": b["原始权重"], "调整后等级": "预警",
                           "调整后权重": b["权重"], "豁免理由": hit_rule.get("豁免理由", "")})
    return adjusted, exempt_log


def overall_risk(alerts: list) -> tuple:
    score = sum(a.get("权重", 0) for a in alerts)
    # 仅“极高/高风险”项直接触发极高风险；“极度异常”按得分参与判定
    severe = {"极高", "高风险"}
    has_severe = any(a.get("等级") in severe for a in alerts)
    if has_severe or score > 60:
        level = "极高风险"
    elif score > 30:
        level = "高风险"
    elif score > 10:
        level = "中风险"
    else:
        level = "低风险"
    n = len(alerts)
    n_exempt = sum(1 for a in alerts if a.get("行业豁免") == "是")
    top = sorted(alerts, key=lambda x: -x.get("权重", 0))[:3]
    top_str = "；".join(f"[{a['等级']}]{a['项目']}" for a in top) if top else "无"
    desc = (f"共触发告警{n}项（其中{n_exempt}项经行业特性豁免降级），加权风险得分{score}。"
            f"{'仍存在极高/高风险项。' if has_severe else ''}主要风险：{top_str}。"
            f"判定规则：存在未豁免的极高/高风险项或得分>60为极高风险，"
            f"得分>30为高风险，>10为中风险，否则低风险。")
    return level, desc


def anomaly_rows(ind: dict, hits: list) -> list:
    rows = []
    hit_by_name = {}
    for h in hits:
        name = h["指标"]
        if name not in hit_by_name or SEVERITY_RANK[h["等级"]] > SEVERITY_RANK[hit_by_name[name]["等级"]]:
            hit_by_name[name] = h
    for disp, key, warn, abn, extreme, hb, w, desc in THRESHOLDS:
        if key not in ind:
            continue
        v = ind[key]
        h = hit_by_name.get(disp)
        if h:
            rows.append({"指标名称": disp, "指标数值": round(v, 6),
                         "异常等级": h["等级"],
                         "异常判定理由": f"{h['说明']}（当前值 {v:.4f}，触发阈值判定）"})
        else:
            rows.append({"指标名称": disp, "指标数值": round(v, 6),
                         "异常等级": "无",
                         "异常判定理由": f"当前值 {v:.4f} 处于合理区间（{desc}），未触发阈值"})
    return rows


def build(company, period, meta, ind, hits, gouji, alerts, evid, paths,
          exempt_log=None, key_info=None) -> dict:
    key_info = key_info or {}
    base_info = {
        "公司名称": company,
        "股票代码": meta.get("股票代码", ""),
        "报告年度": period,
        "所属行业": meta.get("所属行业", ""),
        "审计意见类型": key_info.get("审计意见类型", ""),
        "归母净利润": key_info.get("归母净利润", ""),
        "扣非归母净利润": key_info.get("扣非归母净利润", ""),
        "现金分红_普通股": key_info.get("现金分红_普通股", ""),
        "分红付息现金": key_info.get("分红付息现金", ""),
        "合并范围变化": key_info.get("合并范围变化", ""),
    }
    ind_calc = [{"指标分类": IND_CLASS.get(k, "其他"), "指标名称": k,
                 "指标原始数值": v,
                 "指标计算结果值": round(v, 4) if isinstance(v, float) else v}
                for k, v in ind.items() if not k.startswith("_")]
    ind_abn = anomaly_rows(ind, hits)
    gouji_rows = [{"勾稽项名称": r["勾稽项名称"], "理论计算值": r["理论计算值"],
                   "报表实际值": r["报表实际值"], "差异率": r["差异率"],
                   "勾稽校验结论": r["勾稽校验结论"]} for r in gouji]
    level, desc = overall_risk(alerts)
    risk_info = {"整体风险等级": level, "汇总风险描述": desc,
                 "行业豁免记录": exempt_log or []}

    package = {"基础信息": base_info, "指标计算结果": ind_calc,
               "指标异常结果": ind_abn, "勾稽校验结果": gouji_rows,
               "汇总风险": risk_info}

    paths.mkdir()
    paths.BENCH_JSON.write_text(json.dumps(package, ensure_ascii=False, indent=2, default=str),
                                encoding="utf-8")
    _write_csv(paths.BASE_CSV, [base_info],
               ["公司名称", "股票代码", "报告年度", "所属行业", "审计意见类型",
                "归母净利润", "扣非归母净利润", "现金分红_普通股", "分红付息现金",
                "合并范围变化"])
    _write_csv(paths.IND_CALC_CSV, ind_calc,
               ["指标分类", "指标名称", "指标原始数值", "指标计算结果值"])
    _write_csv(paths.IND_ABN_CSV, ind_abn,
               ["指标名称", "指标数值", "异常等级", "异常判定理由"])
    _write_csv(paths.GOUJI_CSV, gouji_rows,
               ["勾稽项名称", "理论计算值", "报表实际值", "差异率", "勾稽校验结论"])
    _write_csv(paths.RISK_CSV, [{"整体风险等级": level, "汇总风险描述": desc}],
               ["整体风险等级", "汇总风险描述"])
    return package


def _write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)