import sys
from pathlib import Path
import pandas as pd
from draft_parser import parse_draft
from step3_verify import BASELINE, KEYINFO_KEYS, _get_baseline

path = Path(sys.argv[1] if len(sys.argv) > 1 else "drafts/研报草稿.md")
lines, claims, cites = parse_draft(path)
print(f"草稿：{path.name}")
print(f"声明 {len(claims)} 条（数字{sum(claims['声明类型']=='数字')}｜"
      f"倍数{sum(claims['声明类型']=='倍数')}｜口径{sum(claims['声明类型']=='口径')}）；"
      f"引用 {len(cites)} 条")
print("\n== 无基准支持的基准键（将判『无法核实』） ==")
for k in sorted(set(claims["基准键"]) - set(BASELINE) - KEYINFO_KEYS
                - {"市盈率", "市净率", "市销率", "目标价", "总市值", "股价", "总股本"}):
    print(f"  ✗ {k}")
print("\n== 同行多条声明（可能重复计数） ==")
dup = claims[claims.duplicated(["行号"], keep=False)]
for _, r in dup.iterrows():
    print(f"  行{r['行号']}：{r['声明类型']}｜{r['关键词']}")
