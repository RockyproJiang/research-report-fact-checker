import json
import pandas as pd
from config import ReportPaths

CHECK_DIMS = ["数字核对", "单位换算", "口径甄别", "倍数重算", "前后一致", "引用溯源", "审计意见提示"]

def score(errors: list[dict], all_rows: list[dict],
          cites: list[dict] | None = None,
          opinion_alerts: list[dict] | None = None) -> list[dict]:
    n_err = len(errors)
    n_src = sum(1 for e in errors if e.get("基准来源"))
    n_fix = sum(1 for e in errors if e.get("基准数值_元") is not None)
    n_soft = sum(1 for r in all_rows if r["核查结论"] == "待复核")
    # 覆盖度：按实际执行的核查维度统计（含通过项），而非仅错误类型
    used = set()
    num_rows = [r for r in all_rows if r["声明类型"] == "数字" and r["核查结论"] in ("正确", "错误")]
    if num_rows:
        used |= {"数字核对", "单位换算", "前后一致"}
        if any(r["基准键"] in ("净利润", "归母净利润", "扣非归母净利润") for r in num_rows):
            used.add("口径甄别")
    if any(r["声明类型"] == "倍数" for r in all_rows):
        used.add("倍数重算")
    if cites:
        used.add("引用溯源")
    if opinion_alerts or any(r["声明类型"] == "审计" for r in all_rows):
        used.add("审计意见提示")
    return [
        {"维度": "来源定位率", "数值": f"{n_src}/{n_err}={n_src / n_err:.0%}" if n_err else "N/A"},
        {"维度": "建议可执行率(规则版)", "数值": f"{n_fix}/{n_err}={n_fix / n_err:.0%}" if n_err else "N/A"},
        {"维度": "待复核占比(误报控制代理)", "数值": f"{n_soft}/{len(all_rows)}" if all_rows else "0/0"},
        {"维度": "已覆盖核查维度", "数值": f"{len(used)}/7：{'、'.join(sorted(used)) or '无'}"},
    ]

def build(paths: ReportPaths, verify_rows: list[dict], cites: list[dict],
          opinion_alerts: list[dict], llm_md: str, draft_name: str) -> dict:
    errors = [r for r in verify_rows if r["核查结论"] == "错误"]
    unver  = [r for r in verify_rows if r["核查结论"] in ("待复核", "无法核实")]
    sc = score(errors, verify_rows, cites, opinion_alerts)
    paths.SCORE_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(sc).to_csv(paths.SCORE_CSV, index=False, encoding="utf-8-sig")
    pkg = {
        "草稿": draft_name,
        "核查汇总": {
            "声明总数": len(verify_rows),
            "错误数": len(errors),
            "高危": sum(1 for e in errors if e["等级"] == "高危"),
            "中":   sum(1 for e in errors if e["等级"] == "中"),
            "低":   sum(1 for e in errors if e["等级"] == "低"),
            "待复核/无法核实": len(unver),
            "引用待溯源": len(cites),
            "审计意见提示": opinion_alerts,
            "质量自评": sc,
        },
        "错误清单": errors,
        "待复核清单": unver,
        "引用清单": cites,
        "核查覆盖维度": CHECK_DIMS,
    }
    paths.PACK_JSON.write_text(json.dumps(pkg, ensure_ascii=False, indent=2, default=str),
                               encoding="utf-8")
    return pkg
