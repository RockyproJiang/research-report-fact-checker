from __future__ import annotations

import os
import re
import sys

try:
    import pdfplumber
except ImportError:
    raise SystemExit("缺少 pdfplumber，请先执行: python -m pip install pdfplumber")

_UNIT_MULT = {"元": 1.0, "千元": 1e3, "万元": 1e4, "百万元": 1e6, "亿元": 1e8}
_NUM_UNIT = r"(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)\s*(亿元|百万元|万元|千元|元)?"
_UNIT_PAT = re.compile(r"单位[:：]\s*(元|千元|万元|百万元|亿元)")
_MIN_AMOUNT = 1000.0
_DEBUG = os.environ.get("EXTRACT_DEBUG") == "1"


def _dbg(msg: str) -> None:
    if _DEBUG:
        print(f"[extract_key_info] {msg}", file=sys.stderr, flush=True)


_LABELS_NI_ATTRIB = [
    "归属于上市公司股东的净利润",
    "归属于母公司股东的净利润",
    "归属于母公司所有者的净利润",
    "归属于上市公司股东的净亏损",
    "归属于母公司股东的净亏损",
]
_LABELS_NI_DEDUCT = [
    "归属于上市公司股东的扣除非经常性损益的净利润",
    "归属于上市公司股东的扣除非经常性损益后的净利润",
    "归属于母公司股东的扣除非经常性损益的净利润",
    "归属于母公司股东的扣除非经常性损益后的净利润",
    "归属于上市公司股东的扣除非经常性损益的净亏损",
    "归属于母公司股东的扣除非经常性损益的净亏损",
    "扣除非经常性损益后归属于母公司股东的净利润",
    "扣除非经常性损益后归属于上市公司股东的净利润",
    "扣除非经常性损益后的归属于上市公司股东的净利润",
    "扣除非经常性损益后的归属于母公司股东的净利润",
    "扣除非经常性损益的归属于母公司股东的净利润",
    "扣除非经常性损益的归属于上市公司股东的净利润",
    "归属于母公司股东的扣除非经常性损益的净利润",
    "归属于上市公司股东的扣除非经常性损益净利润",
    "扣非后归属于母公司股东的净利润",
]


def _clean(v) -> str:
    if v is None:
        return ""
    return str(v).replace("\n", "").replace("\u00a0", " ").strip()


def _extract_texts(pdf) -> list:
    texts = []
    for page in pdf.pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:
            texts.append("")
    return texts


def _to_yuan(num_str, unit):
    try:
        v = float(str(num_str).replace(",", ""))
    except Exception:
        return None
    return v * _UNIT_MULT.get(unit or "元", 1.0)


def _after_is_percent(text, pos) -> bool:
    return text[pos:pos + 2].lstrip().startswith("%")


def _global_unit(texts) -> str:
    """全局单位回退：取第一份带单位声明页面的单位（报表整体统一单位）。"""
    for text in texts:
        m = _UNIT_PAT.search(text)
        if m:
            return m.group(1)
    return "元"


def _page_unit(text, fallback):
    m = _UNIT_PAT.search(text)
    return m.group(1) if m else fallback


def _has_note_column(text) -> bool:
    head = text[:3000]
    return ("附注" in head) or ("注释" in head)


def _valid_amounts_in_line(line, page_unit):
    """返回行内全部合法金额候选 [(val, unit, has_unit)]，跳过百分数与小数字。"""
    cands = []
    for m in re.finditer(_NUM_UNIT, line):
        if _after_is_percent(line, m.end()):
            continue
        has_unit = bool(m.group(2))
        val = _to_yuan(m.group(1), m.group(2) or page_unit)
        if val is None:
            continue
        if not has_unit and abs(val) < _MIN_AMOUNT:
            continue
        cands.append((val, m.group(2) or page_unit, has_unit))
    return cands


def _pick_amount_from_position(text_after, page_unit):
    for val, unit, has_unit in _valid_amounts_in_line(text_after, page_unit):
        return val, unit, has_unit
    return None, page_unit, False


def _pick_amount_from_line(line, page_unit, has_note_col):
    """【v2】附注列感知：页面带附注/注释列时，行内第1个数字是注释号，取第2个。"""
    cands = _valid_amounts_in_line(line, page_unit)
    if not cands:
        return None, page_unit, False
    idx = 1 if (has_note_col and len(cands) >= 2) else 0
    return cands[idx]


def _fuzzy_label_pattern(label: str) -> str:
    chars = [c for c in label if not c.isspace()]
    return r"\s*".join(re.escape(c) for c in chars)


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
    for text in texts:
        for m in re.finditer(r"审计意见类型", text):
            label = _classify_opinion_text(text[m.end(): m.end() + 40])
            if label:
                return label
    for text in texts:
        if "标准无保留意见" in text:
            return "标准无保留意见"
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


# ---------------- 归母/扣非净利润 ----------------
def _is_main_income_table_page(text: str) -> bool:
    head = text[:2000]
    return ("合并利润表" in head) or ("母公司利润表" in head)


def _collect_candidates_multi(texts: list, labels: list) -> list:
    cands = []
    g_unit = _global_unit(texts)          # 【v2】全局单位回退
    for label in labels:
        fuzzy_pat = re.compile(_fuzzy_label_pattern(label))
        for idx, text in enumerate(texts):
            if not fuzzy_pat.search(text):
                continue
            page_unit = _page_unit(text, g_unit)
            has_note = _has_note_column(text)   # 【v2】附注列感知
            is_main = _is_main_income_table_page(text)
            for lm in fuzzy_pat.finditer(text):
                seg = text[lm.end(): lm.end() + 300]
                gm = re.match(r"[^\d\-\n]{0,40}", seg)
                line = seg[gm.end():].split("\n")[0]
                val, unit, inline = _pick_amount_from_line(line, page_unit, has_note)
                if val is not None:
                    cands.append({"value": val, "unit": unit,
                                  "inline": inline, "page": idx,
                                  "is_main": is_main, "label": label})
                    _dbg(f"标签命中 {label!r} 页{idx+1} 值={val} 单位={unit} "
                         f"主表={is_main} inline={inline} 附注列={has_note}")
    seen, uniq = set(), []
    for c in cands:
        k = round(c["value"], 2)
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    uniq.sort(key=lambda c: (not c["is_main"], not c["inline"], -abs(c["value"])))
    return uniq


def _main_metrics(texts: list, nonrec_val, nonrec_net=None) -> dict:
    """交叉验证：归母-扣非 应约等于 非经常性损益（归母净额口径）。
    同时尝试 净额口径(合计-税-少数) 与 披露口径 两种解释，择优配对。"""
    res = {"归母净利润": "", "扣非归母净利润": "",
           "归母净利润_备注": "", "扣非归母净利润_备注": ""}
    ca = _collect_candidates_multi(texts, _LABELS_NI_ATTRIB)
    cb = _collect_candidates_multi(texts, _LABELS_NI_DEDUCT)
    _dbg(f"归母净利润候选：{[(c['value'], c['page']+1, c['is_main']) for c in ca[:5]]}")
    _dbg(f"扣非归母候选：{[(c['value'], c['page']+1, c['is_main']) for c in cb[:5]]}")

    net_options = []
    if nonrec_net is not None:
        net_options.append(("净额口径(合计-税-少数)", nonrec_net))
    if nonrec_val is not None:
        net_options.append(("披露口径", nonrec_val))

    if net_options and ca and cb:
        best = None
        for a in ca:
            for b in cb:
                diff_ab = a["value"] - b["value"]
                for opt_name, net in net_options:
                    if abs(net) < 1.0:
                        continue
                    err = abs(diff_ab - net) / abs(net)
                    tol = 0.01 if (a["is_main"] and b["is_main"]) else 0.05
                    if err > tol:
                        continue
                    score = (not (a["is_main"] and b["is_main"]), err)
                    if best is None or score < best[0]:
                        best = (score, err, a, b, opt_name)
        if best:
            _, err, a, b, opt_name = best
            res["归母净利润"] = f"{a['value']:.2f}"
            res["扣非归母净利润"] = f"{b['value']:.2f}"
            note = (f"单位:{a['unit']}；已交叉验证（归母-扣非=非经常性[{opt_name}]，"
                    f"误差{err * 100:.2f}%）")
            res["归母净利润_备注"] = note
            res["扣非归母净利润_备注"] = note
            return res

    if ca:
        a = ca[0]
        res["归母净利润"] = f"{a['value']:.2f}"
        src = "合并利润表" if a["is_main"] else ("摘要页" if a["inline"] else "未定")
        res["归母净利润_备注"] = f"单位:{a['unit']}；来源:{src}；未交叉验证，请人工核对"
    if cb:
        b = cb[0]
        res["扣非归母净利润"] = f"{b['value']:.2f}"
        src = "合并利润表" if b["is_main"] else ("摘要页" if b["inline"] else "未定")
        res["扣非归母净利润_备注"] = f"单位:{b['unit']}；来源:{src}；未交叉验证，请人工核对"
    return res


# ---------------- 非经常性损益合计 ----------------
def _sanity_check(nonrec_val, ni_attrib_hint):
    """合理性检查（仅归母>0 时应用倍数阈值，亏损年不误杀）。"""
    if nonrec_val is None:
        return None
    if abs(nonrec_val) > 1e13:
        return None
    if ni_attrib_hint and ni_attrib_hint > 0 and abs(nonrec_val) > ni_attrib_hint * 1.5:
        _dbg(f"非经常性合计 {nonrec_val} 超过归母净利润×1.5 ({ni_attrib_hint * 1.5})，拦截")
        return None
    return nonrec_val


def _nonrec_section_segment(text: str):
    if "非经常性损益" not in text:
        return None, "元"
    head = text[:200].replace("\n", "").replace(" ", "")
    if "采用公允价值计量的项目" in head and \
            "非经常性损益项目" not in head and "非经常性损益明细" not in head:
        return None, "元"
    m = _UNIT_PAT.search(text)
    page_unit = m.group(1) if m else "元"
    start = -1
    for anchor in ["非经常性损益项目和金额", "非经常性损益项目及金额",
                   "非经常性损益项目", "非经常性损益明细"]:
        idx = text.find(anchor)
        if idx >= 0:
            start = idx + len(anchor)
            break
    if start < 0:
        return None, page_unit
    return text[start:start + 2500], page_unit


def _nonrec_total(texts: list, ni_attrib_hint=None):
    for text in texts:
        segment, page_unit = _nonrec_section_segment(text)
        if segment is None:
            continue
        # 【v2】扩充关键词：归母净额口径命名
        for kw in ["归属于母公司股东的非经常性损益净额",
                   "归属于母公司所有者的非经常性损益净额",
                   "非经常性损益合计", "非经常性损益净额"]:
            idx = segment.find(kw)
            if idx >= 0:
                val, _, _ = _pick_amount_from_position(segment[idx + len(kw):], page_unit)
                if val is not None:
                    checked = _sanity_check(val, ni_attrib_hint)
                    if checked is not None:
                        return checked
        idx = segment.find("合计")
        if idx >= 0:
            val, _, _ = _pick_amount_from_position(segment[idx + 2:], page_unit)
            if val is not None:
                checked = _sanity_check(val, ni_attrib_hint)
                if checked is not None:
                    return checked
        for anchor in ["少数股东权益影响额（税后）", "少数股东权益影响额(税后)",
                       "少数股东权益影响额", "减：所得税影响额", "减:所得税影响额"]:
            pos = segment.find(anchor)
            if pos >= 0:
                after = segment[pos:]
                idx2 = after.find("合计")
                if idx2 >= 0:
                    val, _, _ = _pick_amount_from_position(after[idx2 + 2:], page_unit)
                    if val is not None:
                        checked = _sanity_check(val, ni_attrib_hint)
                        if checked is not None:
                            return checked
    return None


def _nonrec_tax_minority(texts: list):
    tax = minority = None
    for text in texts:
        segment, page_unit = _nonrec_section_segment(text)
        if segment is None:
            continue
        if tax is None:
            m = re.search(r"减[:：]?所得税影响额", segment)
            if m:
                tax, _, _ = _pick_amount_from_position(segment[m.end():], page_unit)
        if minority is None:
            m = re.search(r"少数股东权益影响额(?:（税后）|\(税后\))?", segment)
            if m:
                minority, _, _ = _pick_amount_from_position(segment[m.end():], page_unit)
        if tax is not None and minority is not None:
            break
    return tax, minority


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


# ---------------- 合并范围变化 ----------------
def _consolidation_change(texts: list) -> str:
    for text in texts:
        if "合并范围" in text and ("变更" in text or "变化" in text
                                    or "新增" in text or "处置" in text):
            idx = text.find("合并范围")
            return _clean(text[max(0, idx - 30):idx + 400])
    return ""


# ---------------- 总股本 / 现金分红 ----------------
def _share_capital(texts: list):
    pats = [
        r"总股本[：:]\s*([\d,]+(?:\.\d+)?)\s*(万)?股",
        r"期末普通股股份总数[^\d\n]{0,20}([\d,]+)",
        r"股份总数[：:]\s*([\d,]+)\s*(万)?股",
    ]
    for text in texts[:40]:
        for pat in pats:
            m = re.search(pat, text)
            if m:
                v = float(m.group(1).replace(",", ""))
                if m.lastindex and m.lastindex >= 2 and m.group(2) == "万":
                    v *= 1e4
                elif v < 1e6:
                    v *= 1e4
                if v > 0:
                    return v
    return None


def _dividend(texts: list, share_capital, g_unit) -> dict:
    result = {"现金分红_普通股": "", "现金分红_普通股_金额": "", "分红付息现金": ""}
    for text in texts[:30]:
        if "不派发现金红利" in text or "不进行利润分配" in text or "不分配" in text:
            result["现金分红_普通股"] = "0.00"
            result["现金分红_普通股_金额"] = "0.00"
            break
    if not result["现金分红_普通股"]:
        pats = [
            r"每10股派发现金红利[^\n，。]{0,140}",
            r"每10股派[^\n，。]{0,140}",
            r"现金分红金额[^\n，。]{0,140}",
            r"派发现金红利[^\n，。]{0,140}",
        ]
        for text in texts[:30]:
            for pat in pats:
                m = re.search(pat, text)
                if m:
                    result["现金分红_普通股"] = _clean(m.group(0))
                    break
            if result["现金分红_普通股"]:
                break
    if share_capital and result["现金分红_普通股"]:
        m = re.search(r"每10股派(?:发现金红利)?[^0-9\n]{0,15}(\d+(?:\.\d+)?)\s*元",
                      result["现金分红_普通股"])
        if m:
            div = float(m.group(1)) / 10.0 * share_capital
            result["现金分红_普通股_金额"] = f"{div:.2f}"
    for text in texts:
        if "分配股利、利润或偿付利息支付的现金" not in text:
            continue
        page_unit = _page_unit(text, g_unit)   # 【v2】全局单位回退，修复千元单位丢失
        for line in text.split("\n"):
            if "分配股利、利润或偿付利息支付的现金" not in line:
                continue
            val, _, _ = _pick_amount_from_position(line, page_unit)
            if val is not None:
                result["分红付息现金"] = f"{val:.2f}"
                break
        if result["分红付息现金"]:
            break
    return result


def extract_key_info(pdf_path) -> dict:
    info = {
        "审计意见类型": "", "所属行业": "",
        "归母净利润": "", "扣非归母净利润": "",
        "归母净利润_备注": "", "扣非归母净利润_备注": "",
        "非经常性损益合计": "",
        "非经常性损益明细": [], "合并范围变化": "",
        "现金分红_普通股": "", "现金分红_普通股_金额": "",
        "总股本": "", "分红付息现金": "",
    }
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            texts = _extract_texts(pdf)
        _dbg(f"===== 处理 {pdf_path.name}（共 {len(texts)} 页） =====")
        g_unit = _global_unit(texts)
        info["审计意见类型"] = _audit_opinion(texts)
        info["所属行业"] = _industry(texts)
        ca = _collect_candidates_multi(texts, _LABELS_NI_ATTRIB)
        ni_hint = ca[0]["value"] if ca else None

        tax, minority = _nonrec_tax_minority(texts)
        _dbg(f"非经常性税影响={tax}，少数股东影响={minority}")
        nr_total = _nonrec_total(texts, ni_attrib_hint=ni_hint)
        nr_net = None
        if nr_total is not None and (tax is not None or minority is not None):
            nr_net = nr_total - (tax or 0.0) - (minority or 0.0)
            _dbg(f"非经常性净额口径 = {nr_net}")
        if nr_total is not None:
            info["非经常性损益合计"] = f"{nr_total:.2f}"
            _dbg(f"非经常性合计 = {nr_total}")
        else:
            _dbg(f"非经常性合计未提取到（ni_hint={ni_hint}）")

        info.update(_main_metrics(texts, nr_total, nonrec_net=nr_net))
        info["非经常性损益明细"] = _nonrec_items(texts)
        info["合并范围变化"] = _consolidation_change(texts)

        shares = _share_capital(texts)
        if shares:
            info["总股本"] = f"{shares:.0f}"
        div = _dividend(texts, shares, g_unit)
        info["现金分红_普通股"] = div.get("现金分红_普通股", "")
        info["现金分红_普通股_金额"] = div.get("现金分红_普通股_金额", "")
        info["分红付息现金"] = div.get("分红付息现金", "")
    except Exception as exc:
        info["_错误"] = str(exc)
        _dbg(f"异常：{exc}")
    return info