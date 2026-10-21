import sys
import pandas as pd
from step2_standardize import load_alias_map, normalize_item
from config import OUTPUT_DIR

folder = sys.argv[1] if len(sys.argv) > 1 else "贵州茅台：2025年年度报告"
base = OUTPUT_DIR / folder

raw = pd.read_csv(base / "01_原始提取.csv", dtype=str)
alias = load_alias_map()
print("== 一、原始提取中未映射的科目 ==")
for _, r in raw.iterrows():
    stmt = str(r["报表"]).strip()
    if stmt not in ("合并资产负债表", "合并利润表", "合并现金流量表"):
        continue
    item = normalize_item(r["项目"])
    if (stmt, item) not in alias:
        print(f"  未映射: {stmt} | {item}")

std = pd.read_csv(base / "02_标准化数据.csv")
keys = set()
for _, r in std.iterrows():
    stmt, s = r["报表"], r["标准科目"]
    if stmt == "资产负债表":
        if pd.notna(r["本期值_元"]): keys.add(f"{s}_期末")
        if pd.notna(r["上期值_元"]): keys.add(f"{s}_期初")
    elif stmt == "利润表":
        if pd.notna(r["本期值_元"]): keys.add(f"{s}_本期")
        if pd.notna(r["上期值_元"]): keys.add(f"{s}_上期")
    else:
        if pd.notna(r["本期值_元"]): keys.add(s)

print("\n== 二、关键科目覆盖检查（✗=缺失，会导致勾稽/指标缺项） ==")
need = ["总资产_期末", "总负债_期末", "所有者权益_期末", "所有者权益_期初",
        "流动资产_期末", "流动负债_期末", "未分配利润_期末", "未分配利润_期初",
        "营业收入_本期", "营业收入_上期", "营业成本_本期", "营业成本_上期",
        "净利润_本期", "净利润_上期", "营业利润_本期", "利润总额_本期",
        "所得税费用_本期", "经营活动现金净流量", "现金分红",
        "现金及现金等价物_期末", "现金及现金等价物_期初"]
for k in need:
    print(("  ✓ " if k in keys else "  ✗ 缺失 ") + k)

print("\n== 三、利润表『本期有值、上期缺失』的科目（疑似提取内核漏列） ==")
li = std[(std["报表"] == "利润表") & std["本期值_元"].notna() & std["上期值_元"].isna()]
for _, r in li.iterrows():
    print(f"  {r['标准科目']}（原始科目：{r['原始科目']}）")
