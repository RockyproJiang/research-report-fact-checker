# -*- coding: utf-8 -*-
"""草稿解析：支持 .md/.txt/.docx/.pdf，抽取『声明清单』。
每条声明带草稿定位（行号+原文），分五类：
  数字类（收入/利润/资产/现金流…）、倍数类（PE/PB/PS/目标价/市值）、
  口径类（归母/扣非/合并/母公司表述）、单位敏感句、引用类（《》/根据…）。
引用类不做规则判定，登记后交 LLM 溯源（误报控制：规则不判引用真伪）。"""
from __future__ import annotations
import re
from pathlib import Path
import pandas as pd

UNIT_WORDS = {"亿元": 1e8, "百万元": 1e6, "万元": 1e4, "千元": 1e3, "元": 1.0}
NUM = r"(-?[\d,]+(?:\.\d+)?)\s*(亿元|百万元|万元|千元|元)?"

# 指标关键词 → (声明类型, 基准键)  基准键见 step3_verify.BASELINE
IND_PATTERNS = [
    (r"归属于上市公司股东的扣除非经常性损益的?净利润|扣非(归母)?净利润|扣非后净利润", "数字", "扣非归母净利润"),
    (r"归属于(上市公司|母公司)股东的?净利润|归母净利润", "数字", "归母净利润"),
    (r"经营活动(产生的)?现金流量净额|(?<!每股)经营现金流(净额)?", "数字", "经营活动现金净流量"),
    (r"营业(业务)?收入|营收|总收入", "数字", "营业收入"),
    (r"营业(业务)?成本", "数字", "营业成本"),
    (r"(净利润)(?!率|比例)", "数字", "净利润"),
    (r"利润总额", "数字", "利润总额"),
    (r"总资产(?!周转)|资产总额", "数字", "总资产"),
    (r"总负债(?!比|率)|负债总额", "数字", "总负债"),
    (r"(?<!每股)净资产|所有者权益|股东权益", "数字", "所有者权益"),
]
MULT_PATTERNS = [
    (r"市盈率|PE\b|P/E", "市盈率"),
    (r"市净率|PB\b|P/B", "市净率"),
    (r"市销率|PS\b|P/S", "市销率"),
    (r"目标价", "目标价"),
    (r"总市值|市值", "总市值"),
    (r"股价|收盘价", "股价"),
    (r"总股本|股本", "总股本"),
]
CITE_PAT = re.compile(r"《[^》]{2,60}》|(?:根据|依据|据|引自|来源[:：])[^。；\n]{4,80}")
# 表格行判别：3 个以上纯空格分隔的数值 → 多列财务表行（跨年份列），交给指标支线，
# 不在全口径草稿声明中按单值处理（避免跨年/周转率/每股指标误匹配导致的误报）。
_TABLE_ROW = re.compile(r"(?:[-+]?\d[\d,]*(?:\.\d+)?\s+){2,}[-+]?\d[\d,]*(?:\.\d+)?")


def _is_table_row(line: str) -> bool:
    return bool(_TABLE_ROW.search(line))


def _non_overlap_hits(line: str) -> list[tuple]:
    """取一行内全部『数字/倍数』指标命中，重叠区间保留最长关键词
    （如『归母净利润』优先于『净利润』，避免重复计数）。"""
    hits = []
    for pat, stype, base_key in IND_PATTERNS:
        for m in re.finditer(pat, line):
            hits.append((m.start(), m.end(), stype, base_key, m.group(0)))
    for pat, label in MULT_PATTERNS:
        for m in re.finditer(pat, line):
            hits.append((m.start(), m.end(), "倍数", label, m.group(0)))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    kept = []
    for h in hits:
        s, e = h[0], h[1]
        if any(not (e <= ks or s >= ke) for (ks, ke, *_) in kept):
            continue
        kept.append(h)
    return kept

def _unit_mult(unit: str) -> float:
    return UNIT_WORDS.get((unit or "元").strip(), 1.0)

def _read_lines(path: Path) -> list[str]:
    suf = path.suffix.lower()
    if suf in (".md", ".txt", ""):
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    if suf == ".docx":
        try:
            from docx import Document
        except ImportError:
            raise SystemExit("缺少 python-docx：python -m pip install python-docx")
        doc = Document(str(path))
        return [p.text for p in doc.paragraphs if p.text.strip()]
    if suf == ".pdf":
        import pdfplumber
        lines = []
        with pdfplumber.open(str(path)) as pdf:
            for pg in pdf.pages:
                try:
                    t = pg.extract_text() or ""
                except Exception:
                    t = ""
                lines.extend(t.splitlines())
        return lines
    raise SystemExit(f"不支持的草稿格式：{suf}")

def _match_value(line: str, kw_end: int) -> tuple[float | None, str]:
    """从关键词之后同一行取首个非百分比、非年份数字+单位。
    返回 (数值_元, 单位)；单位未在文本中标明时返回空串（由核查侧判单位存疑）。"""
    seg = line[kw_end: kw_end + 140]
    for m in re.finditer(NUM, seg):
        tail = seg[m.end(): m.end() + 2].lstrip()
        if tail.startswith("%"):
            continue
        # 跳过"年份"（数字后紧跟"年/年度/年份"），如"2025年公司…"
        nxt = seg[m.end(): m.end() + 8]
        if re.match(r"\s*年[度份]?", nxt):
            continue
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = m.group(2) or ""
        if not unit:
            # 回看关键词与数值之间的小括号单位（如"总资产(亿元) 306.21"）
            um = re.search(r"(万亿元|亿元|百万元|万元|千元|元)[）)（(]?\s*$", seg[:m.start()])
            if um:
                unit = um.group(1)
        return v * _unit_mult(unit), unit
    return None, ""

def parse_draft(draft_path: Path) -> tuple[list[str], pd.DataFrame, list[str]]:
    """返回 (全文行, 声明清单, 引用清单)"""
    lines = _read_lines(draft_path)
    claims = []
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        if _is_table_row(line):
            continue  # 表格多列行，不按单值声明处理
        # ①+② 数字/倍数类：多指标行取全部不重叠命中
        for s, e, stype, base_key, kw in _non_overlap_hits(line):
            val, unit = _match_value(line, e)
            claims.append({"行号": lineno, "声明类型": stype, "基准键": base_key,
                           "关键词": kw, "草稿数值_元": val, "草稿单位": unit,
                           "原文": line.strip()})
        # ③ 口径类（无数值，仅登记表述，供口径甄别）
        if re.search(r"(归母|扣非|合并报表|母公司报表|摊薄|基本每股|稀释)", line) \
                and re.search(r"净利润|每股|口径", line):
            claims.append({"行号": lineno, "声明类型": "口径", "基准键": "",
                           "关键词": "", "草稿数值_元": None, "草稿单位": "",
                           "原文": line.strip()})
    # ④ 引用类（独立登记）
    cites = []
    for lineno, line in enumerate(lines, 1):
        for m in CITE_PAT.finditer(line):
            cites.append({"行号": lineno, "引用原文": m.group(0).strip()})
    df = pd.DataFrame(claims, columns=["行号", "声明类型", "基准键", "关键词",
                                       "草稿数值_元", "草稿单位", "原文"])
    return lines, df, cites
