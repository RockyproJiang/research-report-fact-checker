import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import config
import step1_baseline
import step2_standardize as s2

SRC = Path(r"C:\Users\rocky\Downloads\7家上市公司年报\贵州茅台：2025年年度报告.pdf")


def log(m):
    print(m)


def main() -> None:
    assert SRC.exists(), f"源年报缺失：{SRC}"
    # 写入 main.py 扫描读取的本地基准缓存目录 output\_基准库_<公司：报告期>\
    # （该目录已含指标支线生成的 03_指标结果.csv，此处补齐含净利润/利润总额的 02）
    paths = config.make_report_paths("_基准库_" + SRC.stem)
    paths.mkdir()
    item = step1_baseline.run_one(SRC, log, paths=paths)
    std = s2.run(item["raw"], log, paths)
    companies, continuity = s2.assemble(std)
    if not companies:
        print("[中止] 标准化为空")
        return
    for comp, reports in companies.items():
        for fname, it in reports.items():
            print(f"[基准] {comp} {fname} 报告期={it['year']}年{it['period']}")
            d = it["d"]
            print("  利润表 净利润_本期(合并) :", d["利润表"].get("净利润_本期"))
            print("  利润表 利润总额_本期   :", d["利润表"].get("利润总额_本期"))
            print("  利润表 营业利润_本期   :", d["利润表"].get("营业利润_本期"))
            print("  利润表 营业收入_本期   :", d["利润表"].get("营业收入_本期"))
            print("  现金流量表 经营活动   :", d["现金流量表"].get("经营活动现金净流量"))
            ki = item["key_info"]
            print("  关键信息 归母净利润    :", ki.get("归母净利润"))
            print("  关键信息 扣非归母净利润:", ki.get("扣非归母净利润"))
            print("  关键信息 审计意见类型  :", ki.get("审计意见类型"))
    print(f"[done] 基准已写入 output\\_基准库_{SRC.stem}\\")


if __name__ == "__main__":
    main()
