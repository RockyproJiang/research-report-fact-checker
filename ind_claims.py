from __future__ import annotations
import re
from pathlib import Path

# ---------- 指标关键词（同义词归一；顺序即优先级） ----------
IND_PATTERNS = [
    ("资本积累率",   re.compile(r"资本积累率")),
    ("资本积累率",   re.compile(r"归母净资产增长率")),          # 规则§三口径差异示例（归母口径）
    ("现金流量比率", re.compile(r"现金流量比率")),
    ("资产负债率",   re.compile(r"资产负债率")),
    ("产权比率",     re.compile(r"产权比率")),
    ("营业现金比率", re.compile(r"营业现金比率")),
    ("流动比率",     re.compile(r"流动比率")),
    ("毛利率",       re.compile(r"销售毛利率|毛利率")),
]
# 附加指标（不在规则§二 8 项基准内，但研报/数据终端常披露）：只留痕观察，不参与8项基准比对。
# 其计算口径与年报基准及不同终端（同花顺/Wind）衍生口径可能不一致，需人工核对（见 EXTRA_NOTE）。
EXTRA_PATTERNS = [
    ("每股经营现金流", re.compile(r"每股经营(活动)?现金(净)?流量|每股经营现金流")),
    ("速动比率",     re.compile(r"速动比率")),
    ("净资产收益率",  re.compile(r"净资产收益率|ROE\b")),
]
# ---------- 上下文标记 ----------
RE_FORECAST = re.compile(r"预计|预测|前瞻|目标价|盈利预测|20\d{2}E\b")
RE_QUARTER  = re.compile(r"\d{2}Q[1-4]|20\d{2}\s*年?\s*Q[1-4]|第?[一二三四1-4]\s*季度|[一二三四]季报|单季|季报")
RE_TTM      = re.compile(r"TTM|滚动", re.I)
RE_ESTIMATE = re.compile(r"测算|估算|倒推")
RE_GUIMU    = re.compile(r"归母|归属于母|归属于上市公司股东")
RE_YEAR4    = re.compile(r"(20\d{2})\s*年")
RE_YEAR2    = re.compile(r"(?<![\d.])(\d{2})\s*年")
RE_YEAR_A   = re.compile(r"(20\d{2})\s*A(?![a-zA-Z])")
# ---------- 数值 ----------
NUM_RE   = re.compile(r"([+-]?\d[\d,]*(?:\.\d+)?)\s*(%|％|倍)?")
# 表格行判定：关键词之后紧跟 3 个及以上纯空格分隔数字（无 %/单位）→ 是表格数据行，
# 由 extract_summary_table 负责，正文抽取器跳过（避免双栏合并行把表数值当正文声明）。
RE_TABLE_ROW = re.compile(
    r"^\s*[+-]?[\d,]+(?:\.\d+)?\s+[+-]?[\d,]+(?:\.\d+)?\s+[+-]?[\d,]+(?:\.\d+)?")

def _is_table_data_window(text: str, start: int) -> bool:
    win = text[start: start + 60]
    # 去掉关键词与数值之间的单位/括号（如"毛利率(%) 91.9 91.2…"），再判 3 个纯数字
    win = re.sub(r"^[\s(（%)％]*", "", win)
    return bool(RE_TABLE_ROW.match(win))
PP_RES = [
    re.compile(r"/\s*([+-]?\d+(?:\.\d+)?)\s*pct", re.I),                      # /-0.75pct
    re.compile(r"([+-]?\d+(?:\.\d+)?)\s*个?百分点"),                          # -0.75个百分点
    re.compile(r"(上升|提升|提高|增加|下降|降低|减少|回落)\s*([+-]?\d+(?:\.\d+)?)\s*个?百分点"),
    re.compile(r"([+-]?\d+(?:\.\d+)?)\s*pp\b", re.I),
]
_NEG_VERB = {"下降", "降低", "减少", "回落"}


# ---------- 读取 ----------
def read_pages(path: Path) -> list[tuple[int | None, str]]:
    """返回 [(页码, 行)]；非PDF页码为 None（位置用行号）。"""
    suf = path.suffix.lower()
    if suf == ".pdf":
        import pdfplumber
        out = []
        with pdfplumber.open(str(path)) as pdf:
            for i, pg in enumerate(pdf.pages, 1):
                try:
                    t = pg.extract_text() or ""
                except Exception:
                    t = ""
                out.extend((i, ln) for ln in t.splitlines())
        return out
    if suf in (".txt", ".md", ""):
        return [(None, ln) for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    if suf == ".docx":
        from docx import Document
        doc = Document(str(path))
        return [(None, p.text) for p in doc.paragraphs if p.text.strip()]
    raise SystemExit(f"不支持的草稿格式：{suf}")


# ---------- 场景上下文 ----------
def _year_of(text: str) -> int | None:
    for pat, off in ((RE_YEAR4, 0), (RE_YEAR_A, 0), (RE_YEAR2, 2000)):
        m = pat.search(text)
        if m:
            return int(m.group(1)) + off
    return None


def _sentence_scan(pages: list[tuple[int | None, str]]):
    """切句并维护场景：返回 [(页码, 句, {scope, year, flags})]。
    句内自带标记优先；句内无任何时间/预测标记时继承场景（解决"公司毛利率89.76%"
    这类承前省略句的单季归属）。"""
    items = []
    ctx_scope, ctx_year = None, None
    for page, line in pages:
        for seg in re.split(r"(?<=[。；;])", line):
            s = seg.strip()
            if len(s) < 4:
                continue
            own_year = _year_of(s)
            own_q = bool(RE_QUARTER.search(s))
            own_f = bool(RE_FORECAST.search(s))
            if own_q:
                scope, year = "单季", own_year or ctx_year
            elif own_f:
                scope, year = "预测", own_year or ctx_year
            elif own_year:
                scope, year = "年度", own_year
            else:
                scope, year = ctx_scope, ctx_year
            # 更新场景（年报/全年场景优先于句中夹杂的季度子句）
            if own_year and re.search(r"年报|全年|年度报告", s):
                ctx_scope, ctx_year = "年度", own_year
            elif own_q:
                ctx_scope, ctx_year = "单季", own_year or ctx_year
            elif own_f:
                ctx_scope, ctx_year = "预测", own_year or ctx_year
            elif own_year:
                ctx_scope, ctx_year = "年度", own_year
            items.append((page, s, {"scope": scope, "year": year}))
    return items


# ---------- 声明抽取 ----------
def _first_value(text: str, start: int):
    """关键词之后取首个带 %/倍 或独立的小数；跳过年份(后随'年')与pp片段。"""
    win = text[start: start + 60]
    for m in NUM_RE.finditer(win):
        tail = win[m.end(): m.end() + 1]
        if tail == "年":
            continue
        if m.group(2) is None and win[m.start(): m.start() + 1] == "/":
            continue                                # '/-0.75pct' 的pp部分，另路处理
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = m.group(2) or ""
        if unit in ("%", "％"):
            v /= 100.0
        return v, (m.group(1) + (m.group(2) or ""))
    return None, ""


def _pp_value(text: str, start: int):
    win = text[start: start + 60]
    for pat in PP_RES:
        m = pat.search(win)
        if not m:
            continue
        try:
            if len(m.groups()) == 2:                # 动词+数值
                v = float(m.group(2))
                if m.group(1) in _NEG_VERB and v > 0:
                    v = -v
            else:
                v = float(m.group(1))
            return v
        except ValueError:
            continue
    return None


def _mk(page, text, ind, val, val_raw, year, flags, source, pos):
    return {"位置": pos, "页码": page, "原文": text.strip(), "指标": ind,
            "数值原文": val_raw, "数值": val,
            "报告期": f"{year}年年报" if year else "",
            "年份": year, "标记": "|".join(flags), "来源": source, "跳过原因": ""}


def extract_claims(pages: list[tuple[int | None, str]]) -> list[dict]:
    claims = []
    items = _sentence_scan(pages)
    for page, sent, sc in items:
        flags = []
        if sc["scope"] == "预测":
            flags.append("预测")
        if sc["scope"] == "单季":
            flags.append("单季度")
        if RE_TTM.search(sent):
            flags.append("TTM")
        if RE_ESTIMATE.search(sent):
            flags.append("估算")
        if RE_GUIMU.search(sent):
            flags.append("归母")
        pos = f"第{page}页" if page else f"行{len(claims)+1}"
        matched = False
        for ind, pat in IND_PATTERNS:
            m = pat.search(sent)
            if not m:
                continue
            val, val_raw = _first_value(sent, m.end())
            if val is None:
                continue
            if _is_table_data_window(sent, m.end()):
                matched = True
                break                       # 表格行交给 extract_summary_table，正文不重复抽取
            claims.append(_mk(page, sent, ind, val, val_raw, sc["year"], flags, "正文", pos))
            if ind == "毛利率":
                pp = _pp_value(sent, m.end())
                if pp is not None:
                    claims.append(_mk(page, sent, "毛利率同比pp", pp, f"{pp:+.2f}pct",
                                      sc["year"], flags, "正文", pos))
            matched = True
            break                                   # 一句只归属首个命中指标
        if matched:
            continue
        for ind, pat in EXTRA_PATTERNS:             # 附加指标：同样走正文/表格分流
            m = pat.search(sent)
            if not m:
                continue
            val, val_raw = _first_value(sent, m.end())
            if val is None:
                continue
            if _is_table_data_window(sent, m.end()):
                break
            claims.append(_mk(page, sent, ind, val, val_raw, sc["year"], flags, "正文", pos))
            break
    return claims


# ---------- 财务摘要表（表头 2024A/2025A/2026E… 逐列绑定） ----------
RE_COL = re.compile(r"(20\d{2})\s*([AEae])(?![a-zA-Z])")
RE_ROW = re.compile(r"^([^\d\s][^\d]{1,24}?)\s{1,}((?:[+-]?[\d,]+(?:\.\d+)?\s+){2,}[+-]?[\d,]+(?:\.\d+)?)\s*$")
# 比率表表头：财务摘要和估值指标 / 主要财务比率 两类
RE_TBL_HEADER = re.compile(r"指标|项目|主要财务比率|财务摘要")


def _header_table_name(line: str) -> str:
    if "主要财务比率" in line:
        return "主要财务比率表"
    return "财务摘要表"


def _word_bands(words: list, gap: float = 3.0) -> list[list]:
    """按 top 分带（容差 gap 像素），同一视觉行的词归入一带。"""
    bands = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if bands and w["top"] - bands[-1]["top"] <= gap:
            bands[-1]["words"].append(w)
        else:
            bands.append({"top": w["top"], "words": [w]})
    return bands


def _extract_table_by_words(page, page_no: int, cols: list, tbl_name: str,
                            header_word: str) -> list[dict]:
    """按词坐标重建表行：研报表格常无框线（extract_tables 只回表头），
    且第5页为左（资产负债表/现金流量表）+右（利润表/主要财务比率）双栏合并排版。
    以表头关键词所在栏为栏位边界，只取该栏字词，重组 label+数值 行。"""
    try:
        words = page.extract_words() or []
    except Exception:
        return []
    if not words:
        return []
    bands = _word_bands(words)
    # 定位表头带：含表头关键词词且含 ≥2 个年份标记词（2024A/2025A/…）
    hdr_band = None
    for b in bands:
        hw = [w for w in b["words"] if header_word and w["text"] == header_word]
        yrs = [w for w in b["words"] if RE_COL.fullmatch(w["text"] or "")]
        if hw and len(yrs) >= 2:
            hdr_band = b
            break
    if hdr_band is None:
        return []
    # 栏位左界取表头关键词 x0（同栏标签列），右界取年份标记 x1
    hw = next(w for w in hdr_band["words"] if w["text"] == header_word)
    col_min = hw["x0"] - 10.0
    col_max = max(w["x1"] for w in hdr_band["words"] if RE_COL.fullmatch(w["text"] or "")) + 10.0
    hdr_bottom = max(w["bottom"] for w in hdr_band["words"])
    out = []
    for b in bands:
        if b["top"] <= hdr_bottom - 2:
            continue
        if any(w["text"] == "资料来源" or "免责" in w["text"] for w in b["words"]):
            break
        ws = [w for w in b["words"] if w["x0"] >= col_min and w["x1"] <= col_max]
        if not ws:
            continue
        line = " ".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"]))
        rm = RE_ROW.match(line)
        if not rm:
            continue
        label, numstr = rm.group(1).strip(), rm.group(2)
        nums = [float(x.replace(",", "")) for x in numstr.split()]
        if len(nums) != len(cols):
            continue
        ind = next((k for k, p in IND_PATTERNS if p.search(label)), None)
        if ind is None:
            ind = next((k for k, p in EXTRA_PATTERNS if p.search(label)), None)
        if not ind:
            continue
        pct = "%" in label or "％" in label
        for (yr, ae), v in zip(cols, nums):
            flags = ["预测"] if ae == "E" else []
            val = v / 100.0 if pct else v
            out.append(_mk(page_no, line.strip(), ind, val, f"{v}{'%' if pct else ''}",
                           yr, flags, tbl_name, f"第{page_no}页"))
    return out


def extract_summary_table(pages: list[tuple[int | None, str]],
                          page_objs: dict[int, object] | None = None) -> list[dict]:
    claims, i = [], 0
    while i < len(pages):
        page, line = pages[i]
        cols = [(int(m.group(1)), m.group(2).upper()) for m in RE_COL.finditer(line)]
        if len(cols) >= 2 and RE_TBL_HEADER.search(line):
            tbl_name = _header_table_name(line)
            m_hw = RE_TBL_HEADER.search(line)
            header_word = m_hw.group(0) if m_hw else tbl_name
            page_obj = page_objs.get(page) if page_objs and page is not None else None
            if page_obj is not None:
                claims.extend(_extract_table_by_words(page_obj, page, cols, tbl_name,
                                                      header_word))
                i += 1
                continue
            # 无 pdf 对象时的文本行兜底
            j = i + 1
            while j < len(pages):
                p2, l2 = pages[j]
                if re.search(r"资料来源|敬请参阅|免责", l2):
                    break
                rm = RE_ROW.match(l2.strip())
                if not rm:
                    j += 1
                    if j - i > 40:
                        break
                    continue
                label, numstr = rm.group(1).strip(), rm.group(2)
                nums = [float(x.replace(",", "")) for x in numstr.split()]
                j += 1
                if len(nums) != len(cols):
                    continue
                ind = next((k for k, p in IND_PATTERNS if p.search(label)), None)
                if ind is None:
                    ind = next((k for k, p in EXTRA_PATTERNS if p.search(label)), None)
                if not ind:
                    continue
                pct = "%" in label or "％" in label
                for (yr, ae), v in zip(cols, nums):
                    flags = ["预测"] if ae == "E" else []
                    val = v / 100.0 if pct else v
                    claims.append(_mk(p2, l2.strip(), ind, val, f"{v}{'%' if pct else ''}",
                                      yr, flags, tbl_name, f"第{p2}页" if p2 else "表格"))
            i = j
        else:
            i += 1
    # 由相邻两年A列毛利率推导 毛利率同比pp（规则§4.3：先算差值再比对）
    gm = {}
    for c in claims:
        if c["指标"] == "毛利率" and "预测" not in c["标记"]:
            gm.setdefault(c["年份"], []).append(c["数值"])
    for yr in sorted(gm):
        if yr - 1 in gm:
            # 同一年份若多表给出不同舍入值（如正文 91.18% 与表 91.2%），
            # 推导只取基准表口径：多个值时取更接近真实值的（即同数值取唯一值，不同值取首见）
            v_cur = gm[yr][0]
            v_pre = gm[yr - 1][0]
            pp = (v_cur - v_pre) * 100.0
            claims.append({"位置": "财务摘要表(推导)", "页码": None,
                           "原文": f"由摘要表 {yr-1}A 与 {yr}A 毛利率差值推导（{v_pre*100:.2f}% → {v_cur*100:.2f}%）",
                           "指标": "毛利率同比pp", "数值原文": f"{pp:+.2f}pct", "数值": pp,
                           "报告期": f"{yr}年年报", "年份": yr, "标记": "",
                           "来源": "推导", "跳过原因": ""})
    return claims


# ---------- 公司归属 ----------
def detect_company(path: Path, pages: list[tuple[int | None, str]],
                   known: list[str]) -> str | None:
    stem = path.stem
    for n in known:
        if n and n in stem:
            return n
    head = "\n".join(t for p, t in pages[:120] if p is None or p <= 2)
    for n in known:
        if n and n in head:
            return n
    return None


def parse_draft(path: Path, known: list[str]):
    """总入口：返回 (公司简称, 页面行, 声明清单)。
    PDF 额外持有 pdfplumber 页对象，供双栏表格按词坐标重建（须在文档打开期间使用）。"""
    pages = read_pages(path)
    comp = detect_company(path, pages, known)
    claims = extract_claims(pages)
    if path.suffix.lower() == ".pdf":
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            page_objs = {i: pg for i, pg in enumerate(pdf.pages, 1)}
            claims += extract_summary_table(pages, page_objs)
    else:
        claims += extract_summary_table(pages, None)
    # 去重：同指标同数值同报告期同标记只留一条（同一底层数值在不同表格/段落重复出现折叠为一条；
    # 保留"标记"以区分"正文预测"与"表格实际值"，如每股经营现金流48.09 正文被标预测、比率表未标预测，二者都要留）
    seen, out = set(), []
    for c in claims:
        key = (c["指标"], round(c["数值"], 6), c["报告期"], c["标记"])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return comp, pages, out
