from __future__ import annotations

import re

try:
    import pdfplumber
except ImportError:
    raise SystemExit("缺少 pdfplumber，请先执行: python -m pip install pdfplumber")

_UNIT_MULT = {"元": 1.0, "千元": 1e3, "万元": 1e4, "百万元": 1e6, "亿元": 1e8}
_NUM_UNIT = r"(-?[\d,]+\.?\d*)\s*(亿元|百万元|万元|千元|元)?"
_UNIT_PAT = re.compile(r"单位[:：]\s*(元|千元|万元|百万元|亿元)")


def _clean(v) -> str:
    if v is None:
        return ""
    return str(v).replace("\n", "").replace("\u00a0", " ").strip()


def _extract_texts(pdf) -> list:
    """整本PDF文本一次性提取，失败页记空串"""
    texts = []
    for page in pdf.pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:
            texts.append("")
    return texts


def _to_yuan(num_str, unit):
    """数字字符串+单位 → 元（float）"""
    try:
        v = float(str(num_str).replace(",", ""))
    except Exception:
        return None
    return v * _UNIT_MULT.get(unit or "元", 1.0)


def _after_is_percent(text, pos) -> bool:
    """pos 位置之后紧跟% → 该数字是同比增幅列，不是金额"""
    return text[pos:pos + 2].lstrip().startswith("%")


# ---------------- 审计意见 ----------------
_RESERVED_RE = re.compile(r"(?<!无)保留意见")


def _classify_opinion_text(raw: str) -> str:
    if "标准无保留" in raw:
        return "标准无保留意见"
    if "无法表示意见" in raw:
        return "无法表示意见"
    if "否定意见" in raw:
        return "否定意见"
    if "带强调事项段" in raw:
        return "带强调事项段的无保留意见"
    if _RESERVED_RE.search(raw):
        return "保留意见"
    if "无保留意见" in raw:
        return "无保留意见"
    return ""


def _audit_opinion(texts: list) -> str:
    # 1) 显式"审计意见类型"标注后40字符内找意见词
    for text in texts:
        for m in re.finditer(r"审计意见类型", text):
            label = _classify_opinion_text(text[m.end(): m.end() + 40])
            if label:
                return label
    # 2) 全文先查标准意见（必须先于非标，防子串误判）
    for text in texts:
        if "标准无保留意见" in text:
            return "标准无保留意见"
    # 3) 全文查其他非标
    for key in ("带强调事项段", "无法表示意见", "否定意见"):
        for text in texts:
            if key in text:
                return "带强调事项段的无保留意见" if key == "带强调事项段" else key
    for text in texts:
        if _RESERVED_RE.search(text):
            return "保留意见"
    for text in texts:
        if "无保留意见" in text:
            return "无保留意见"
    return ""


# ---------------- 所属行业 ----------------
def _industry(texts: list) -> str:
    pats = [
        r"所属行业[：:]\s*([^\n，。；]+)",
        r"行业分类[：:]\s*([^\n，。；]+)",
        r"公司所处行业[：:]\s*([^\n，。；]+)",
        r"所处行业[：:]\s*([^\n，。；]+)",
    ]
    for text in texts[:30]:
        for pat in pats:
            m = re.search(pat, text)
            if m:
                return _clean(m.group(1))
    return ""


# ---------------- 归母/扣非净利润：多候选 + 交叉验证 ----------------
def _collect_candidates(texts: list, label: str) -> list:
    """在含 label 的页面（前40页），沿同一行收集 label 之后第一个非%数字的候选"""
    cands = []
    pat_label = re.compile(re.escape(label))
    for idx, text in enumerate(texts[:40]):
        if label not in text:
            continue
        um = _UNIT_PAT.search(text)
        page_unit = um.group(1) if um else "元"
        for lm in pat_label.finditer(text):
            seg = text[lm.end(): lm.end() + 200]
            gm = re.match(r"[^\d\-\n]{0,40}", seg)
            line = seg[gm.end():].split("\n")[0]     # 只取同一行
            for m in re.finditer(_NUM_UNIT, line):
                if _after_is_percent(line, m.end()):
                    continue                          # 同比增幅列，跳过
                unit = m.group(2) or page_unit
                val = _to_yuan(m.group(1), unit)
                if val is not None:
                    cands.append({"value": val, "unit": unit,
                                  "inline": bool(m.group(2)), "page": idx})
                    break                             # 每个label位置只取第一个有效金额
    # 按数值去重（摘要页与主表同值合并）
    seen, uniq = set(), []
    for c in cands:
        k = round(c["value"], 2)
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def _main_metrics(texts: list, nonrec_val) -> dict:
    lab_a = "归属于上市公司股东的净利润"
    lab_b = "归属于上市公司股东的扣除非经常性损益的净利润"
    res = {"归母净利润": "", "扣非归母净利润": "",
           "归母净利润_备注": "", "扣非归母净利润_备注": ""}
    ca = _collect_candidates(texts, lab_a)
    cb = _collect_candidates(texts, lab_b)

    # 交叉验证：归母 - 扣非 ≈ 非经常性损益合计
    if nonrec_val and ca and cb:
        best = None
        for a in ca:
            for b in cb:
                err = abs(a["value"] - b["value"] - nonrec_val) / max(abs(nonrec_val), 1.0)
                if best is None or err < best[0]:
                    best = (err, a, b)
        if best and best[0] < 0.05:
            err, a, b = best
            res["归母净利润"] = f"{a['value']:.2f}"
            res["扣非归母净利润"] = f"{b['value']:.2f}"
            note = (f"单位:{a['unit']}；已交叉验证（归母-扣非=非经常合计，"
                    f"误差{err * 100:.2f}%）")
            res["归母净利润_备注"] = note
            res["扣非归母净利润_备注"] = note
            return res

    # 兜底：优先"同页含另一标签"的候选（主要会计数据表特征），其次行内带单位的
    def _pick(cands, other_label):
        if not cands:
            return None, ""
        table = [c for c in cands if other_label in texts[c["page"]]]
        pool = table or cands
        inline = [c for c in pool if c["inline"]]
        chosen = (inline or pool)[0]
        src = "；来源:主要会计数据表" if table else ""
        return chosen, f"单位:{chosen['unit']}{src}；未交叉验证，请人工核对"

    a, na = _pick(ca, lab_b)
    b, nb = _pick(cb, lab_a)
    if a:
        res["归母净利润"] = f"{a['value']:.2f}"
        res["归母净利润_备注"] = na
    if b:
        res["扣非归母净利润"] = f"{b['value']:.2f}"
        res["扣非归母净利润_备注"] = nb
    return res


# ---------------- 非经常性损益：合计 + 明细 ----------------
def _nonrec_total(texts: list):
    """抓"非经常性损益合计/净额"行的本期金额（合计已含所得税、少数股东权益减项）"""
    for text in texts:
        if "非经常性损益" not in text:
            continue
        um = _UNIT_PAT.search(text)
        page_unit = um.group(1) if um else "元"
        for line in text.split("\n"):
            if "非经常性损益" not in line:
                continue
            if "合计" not in line and "净额" not in line:
                continue
            pos = max(line.find("合计"), line.find("净额"))
            seg = line[pos:]
            for m in re.finditer(_NUM_UNIT, seg):
                if _after_is_percent(seg, m.end()):
                    continue
                val = _to_yuan(m.group(1), m.group(2) or page_unit)
                if val is not None:
                    return val
    return None


_NONREC_KEYS = [
    "非流动性资产处置损益", "越权审批或无正式批准文件的税收返还、减免",
    "计入当期损益的政府补助", "计入当期损益的对非金融企业收取的资金占用费",
    "非货币性资产交换损益", "委托他人投资或管理资产的损益",
    "债务重组损益", "企业重组费用", "受托经营取得的托管费收入",
    "单独进行减值测试的应收款项减值准备转回", "对外委托贷款取得的损益",
    "除上述各项之外的其他营业外收入和支出",
    "其他符合非经常性损益定义的损益项目", "非经常性损益合计",
    "减：所得税影响额", "少数股东权益影响额",
]


def _nonrec_items(texts: list) -> list:
    rows = []
    for text in texts:
        if "非经常性损益项目" not in text and "非经常性损益明细" not in text:
            continue
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            for kw in _NONREC_KEYS:
                if kw in line:
                    rows.append(line)
                    break
    seen, uniq = set(), []
    for r in rows:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


# ---------------- 合并范围变化 / 现金分红 ----------------
def _consolidation_change(texts: list) -> str:
    for text in texts:
        if "合并范围" in text and ("变更" in text or "变化" in text or "新增" in text or "处置" in text):
            idx = text.find("合并范围")
            return _clean(text[max(0, idx - 30):idx + 400])
    return ""


def _dividend(texts: list) -> str:
    pats = [
        r"每10股派[^。\n]{0,140}",
        r"每10股派发现金红利[^。\n]{0,140}",
        r"现金分红金额[^\n，。]{0,140}",
        r"派发现金红利[^\n，。]{0,140}",
    ]
    for text in texts[:30]:
        for pat in pats:
            m = re.search(pat, text)
            if m:
                return _clean(m.group(0))
    return ""


def extract_key_info(pdf_path) -> dict:
    info = {
        "审计意见类型": "", "所属行业": "",
        "归母净利润": "", "扣非归母净利润": "",
        "归母净利润_备注": "", "扣非归母净利润_备注": "",
        "非经常性损益合计": "",
        "非经常性损益明细": [], "合并范围变化": "", "现金分红": "",
    }
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            texts = _extract_texts(pdf)
        info["审计意见类型"] = _audit_opinion(texts)
        info["所属行业"] = _industry(texts)
        nr_total = _nonrec_total(texts)
        if nr_total is not None:
            info["非经常性损益合计"] = f"{nr_total:.2f}"
        info.update(_main_metrics(texts, nr_total))
        info["非经常性损益明细"] = _nonrec_items(texts)
        info["合并范围变化"] = _consolidation_change(texts)
        info["现金分红"] = _dividend(texts)
    except Exception as exc:
        info["_错误"] = str(exc)
    return info
