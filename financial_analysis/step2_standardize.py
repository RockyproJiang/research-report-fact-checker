import re
import pandas as pd
from config import MAPPING_CSV

UNIT_MULT = {"元": 1.0, "千元": 1e3, "万元": 1e4, "百万元": 1e6, "亿元": 1e8}

PERIOD_PATTERNS = [
    (re.compile(r"第[一1]季度|一季报|1季度"), "一季报"),
    (re.compile(r"半年度|中期|中报"), "半年报"),
    (re.compile(r"第[三3]季度|三季报|3季度"), "三季报"),
    (re.compile(r"第[四4]季度|四季报"), "四季报"),
    (re.compile(r"年度|年报"), "年报"),
]
PERIOD_ORDER = {"一季报": 1, "半年报": 2, "三季报": 3, "四季报": 4, "年报": 4}

PREFIX_RE = re.compile(r"^(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\([一二三四五六七八九十]+\)"
                       r"|\d+[.．、]|（\d+）|\(\d+\)|其中[:：]|减[:：]|加[:：])")

TARGET_STMTS = ("合并资产负债表", "合并利润表", "合并现金流量表")

STMT_CANON = {
    "合并资产负债表": "资产负债表",
    "合并利润表": "利润表",
    "合并现金流量表": "现金流量表",
}

STD_COLS = ["文件名", "公司名称", "年份", "报告期", "期序", "报表", "标准科目",
            "原始科目", "本期值_元", "上期值_元", "原单位", "数据位置"]

# 同一标准科目出现多行时的取舍优先级（数字越小越优先）。
# 所有者权益：优先"合计"口径（含少数股东），归母口径已单列标准科目；
# 营业收入：优先"营业收入"（"营业总收入"为其父级，非金融企业两者相等）。
_DUP_PRIORITY = {
    "所有者权益": ["所有者权益合计", "股东权益合计", "所有者权益（或股东权益）合计",
                "所有者权益(或股东权益)合计"],
    "营业收入": ["营业收入", "营业总收入"],
}


def parse_period(filename: str):
    """从文件名解析 (年份, 报告期)"""
    year_m = re.search(r"(20\d{2})", filename)
    year = int(year_m.group(1)) if year_m else None
    period = "年报"
    for pat, name in PERIOD_PATTERNS:
        if pat.search(filename):
            period = name
            break
    return year, period


def _full_to_half(s: str) -> str:
    result = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            code = 32
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        result.append(chr(code))
    s = "".join(result)
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\uff0d", "-").replace("\u2014", "-").replace("\u2212", "-")
    return s


def normalize_item(name: str) -> str:
    s = str(name).strip().replace(" ", "")
    s = _full_to_half(s)
    for _ in range(3):
        m = PREFIX_RE.match(s)
        if not m:
            break
        s = s[m.end():].strip()
    s = re.sub(r"[（(]亏损[^）)]*[）)]", "", s)
    s = re.sub(r"[（(]净亏损[^）)]*[）)]", "", s)
    s = re.sub(r"[（(]亏损总额[^）)]*[）)]", "", s)
    s = re.sub(r"[（(]净亏损以[^）)]*[）)]", "", s)
    return s.strip()


def load_alias_map():
    alias2std = {}
    if not MAPPING_CSV.exists():
        raise SystemExit(f"找不到映射表：{MAPPING_CSV}")
    import csv as _csv
    with open(MAPPING_CSV, encoding="utf-8-sig") as f:
        reader = _csv.reader(f)
        next(reader, None)
        for lineno, row in enumerate(reader, start=2):
            if not row or not any(c.strip() for c in row):
                continue
            if len(row) < 3:
                print(f"[warn] 映射表第{lineno}行列数不足({len(row)}列)，已跳过：{row}")
                continue
            std, stmt = row[0].strip(), row[1].strip()
            aliases = ",".join(c.strip() for c in row[2:])
            for a in aliases.split("|"):
                a = a.strip()
                if a:
                    alias2std[(stmt, a)] = std
    return alias2std


def _dup_score(std: str, raw: str):
    pri = _DUP_PRIORITY.get(std, [])
    return (pri.index(raw) if raw in pri else len(pri),
            ("归属于母公司" in raw) or ("母公司" in raw),
            -len(raw))


def _resolve_dupes(rows: list) -> list:
    """同一 (报表, 标准科目) 多行时确定性取舍，避免 PDF 行序决定结果。"""
    best = {}
    for r in rows:
        key = (r["报表"], r["标准科目"])
        sc = _dup_score(r["标准科目"], r["原始科目"])
        if key not in best or sc < best[key][0]:
            best[key] = (sc, r)
    return [v[1] for v in best.values()]


def run(raw_df: pd.DataFrame, log, paths) -> pd.DataFrame:
    """标准化单份报告的原始数据，CSV写入 paths.STD_CSV"""
    alias2std = load_alias_map()
    out = []
    for _, r in raw_df.iterrows():
        stmt_raw = str(r["报表"]).strip()
        if stmt_raw not in TARGET_STMTS:
            continue
        stmt = STMT_CANON.get(stmt_raw, stmt_raw)
        raw_item = normalize_item(r["项目"])
        std = alias2std.get((stmt, raw_item))
        if std is None:
            raw_item2 = re.sub(r"[（(][^）)]*[）)]", "", raw_item).strip()
            std = alias2std.get((stmt, raw_item2))
        if std is None:
            continue
        unit = str(r.get("单位", "元") or "元")
        mult = UNIT_MULT.get(unit, 1.0)
        cur = pd.to_numeric(r.get("本期金额"), errors="coerce")
        pre = pd.to_numeric(r.get("上期金额"), errors="coerce")
        year, period = parse_period(str(r["文件名"]))
        out.append({
            "文件名": r["文件名"], "公司名称": r["公司名称"],
            "年份": year, "报告期": period, "期序": PERIOD_ORDER.get(period, 4),
            "报表": stmt, "标准科目": std, "原始科目": str(r["项目"]).strip(),
            "本期值_元": cur * mult if pd.notna(cur) else None,
            "上期值_元": pre * mult if pd.notna(pre) else None,
            "原单位": unit, "数据位置": r.get("数据位置", ""),
        })
    out = _resolve_dupes(out)
    df = pd.DataFrame(out, columns=STD_COLS)
    df.to_csv(paths.STD_CSV, index=False, encoding="utf-8-sig")
    if df.empty:
        log(f"[step2] ⚠️ {paths.report_name} 标准化 0 行："
            f"请检查 01_原始提取.csv 的『报表/项目』列与映射表是否匹配")
    else:
        log(f"[step2] {paths.report_name}：标准化 {len(df)} 行 → {paths.STD_CSV.name}")
    return df


# ---------- 组装数据字典 ----------
def assemble(std_df: pd.DataFrame):
    """【必须在合并了多家多期报告的标准化大表上调用】（main 已保证）。
    返回 {公司: {文件名: {...}}} 以及跨报告衔接差异列表 continuity。"""
    if std_df is None or std_df.empty:
        return {}, []
    companies = {}
    for (comp, fname), g in std_df.groupby(["公司名称", "文件名"]):
        year, period = parse_period(fname)
        d = {"资产负债表": {}, "利润表": {}, "现金流量表": {}}
        evid = {}
        for _, r in g.iterrows():
            stmt, std = r["报表"], r["标准科目"]
            pos = r["数据位置"]
            if stmt == "资产负债表":
                if pd.notna(r["本期值_元"]):
                    d[stmt][f"{std}_期末"] = float(r["本期值_元"])
                    evid[f"{std}_期末"] = pos
                if pd.notna(r["上期值_元"]):
                    d[stmt][f"{std}_期初"] = float(r["上期值_元"])
                    evid[f"{std}_期初"] = pos
            elif stmt == "利润表":
                if pd.notna(r["本期值_元"]):
                    d[stmt][f"{std}_本期"] = float(r["本期值_元"])
                    evid[f"{std}_本期"] = pos
                if pd.notna(r["上期值_元"]):
                    d[stmt][f"{std}_上期"] = float(r["上期值_元"])
                    evid[f"{std}_上期"] = pos
            else:
                if pd.notna(r["本期值_元"]):
                    d[stmt][std] = float(r["本期值_元"])
                    evid[std] = pos
        companies.setdefault(comp, {})[fname] = {
            "year": year, "period": period, "d": d, "evid": evid, "ring_prior": None}

    continuity = []
    for comp, reports in companies.items():
        order = sorted(reports.items(),
                       key=lambda kv: (kv[1]["year"] or 0, PERIOD_ORDER.get(kv[1]["period"], 4)))
        by_yp = {(it["year"], it["period"]): fn for fn, it in order}
        for i in range(1, len(order)):
            order[i][1]["ring_prior"] = order[i - 1][0]

        # ---- 【修复】上年初 = (上年, 同报告期) 报告的资产负债表期初 ----
        # 同比口径：本期(如2025H1)与上期(2024H1)的对比期起点分别是各自年度起点，
        # 因此 e2 必须取上年【同报告期】的期初，而非时间上上一份报告（可能是本年一季报）的期初。
        for fname, cur in order:
            if cur["year"] is None:
                continue
            src = by_yp.get((cur["year"] - 1, cur["period"]))
            if not src:
                continue
            for k, v in reports[src]["d"]["资产负债表"].items():
                if k.endswith("_期初"):
                    cur["d"]["资产负债表"].setdefault(k[:-3] + "_上年初", v)
                elif k.endswith("_期末"):
                    # _上年末 = 上年同报告期期末（= 本期期初的理论值，衔接校验用）
                    cur["d"]["资产负债表"].setdefault(k[:-3] + "_上年末", v)

        # ---- 期初/期末一致性校验（跨报告衔接：追溯调整/重述/口径变化） ----
        for i in range(1, len(order)):
            prev_f, prev = order[i - 1]
            cur_f, cur = order[i]
            dp, dc = prev["d"], cur["d"]
            for key, base in [("现金及现金等价物_期末", "现金及现金等价物_期初"),
                              ("所有者权益_期末", "所有者权益_期初"),
                              ("总资产_期末", "总资产_期初"),
                              ("未分配利润_期末", "未分配利润_期初")]:
                a = dp["现金流量表"].get(key) or dp["资产负债表"].get(key)
                b = dc["现金流量表"].get(base) or dc["资产负债表"].get(base)
                if a and b and abs(a - b) / abs(a) > 0.005:
                    continuity.append({
                        "公司": comp, "当前报告": cur_f, "上一报告": prev_f,
                        "科目": base, "上期期末": a, "本期期初": b,
                        "差异%": f"{abs(a - b) / abs(a) * 100:.2f}%",
                        "说明": "跨报告期初/期末不一致，可能存在追溯调整、重述或口径变化"})
    return companies, continuity


def extract_nonrec(raw_df: pd.DataFrame):
    rows = []
    for _, r in raw_df.iterrows():
        item = str(r["项目"])
        if "非经常性损益" in item or "政府补助" in item:
            if "合计" in item:
                continue
            cur = pd.to_numeric(r.get("本期金额"), errors="coerce")
            rows.append({"文件名": r["文件名"], "公司名称": r["公司名称"],
                         "项目": item.strip(), "本期金额": cur,
                         "单位": r.get("单位", "元"), "数据位置": r.get("数据位置", "")})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import config
    from pathlib import Path
    raw = pd.read_csv(Path("output") / "raw_tmp.csv", dtype=str)
    run(raw, print, config.make_report_paths("tmp"))