import re
import threading
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
COMPANY_META_CSV     = BASE_DIR / "mapping" / "company_meta.csv"
INDUSTRY_KEYWORDS_CSV = BASE_DIR / "mapping" / "industry_keywords.csv"

_WRITE_LOCK = threading.Lock()   # 保护 company_meta.csv 的读改写

_CODE_PAT = re.compile(
    r"(?:股票代码|公司代码|证券代码|证券简称|股票简称)[：:（(]\s*(\d{6})\s*[）)]?")


def _exchange_suffix(code6: str) -> str:
    if code6.startswith(("60", "68")):
        return ".SH"
    if code6.startswith(("00", "30")):
        return ".SZ"
    if code6.startswith(("43", "83", "87", "92")):
        return ".BJ"
    return ""


def extract_code_from_pdf(pdf_path) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages[:3]:
                text = page.extract_text() or ""
                for m in _CODE_PAT.finditer(text):
                    code6 = m.group(1)
                    suffix = _exchange_suffix(code6)
                    return code6 + suffix if suffix else code6
    except Exception:
        pass
    return ""


DEFAULT_IND_KEYWORDS = [
    # 白酒
    ("贵州茅台", "白酒"), ("五粮液", "白酒"), ("泸州老窖", "白酒"), ("山西汾酒", "白酒"),
    ("洋河", "白酒"), ("古井", "白酒"), ("舍得", "白酒"), ("酒鬼酒", "白酒"), ("水井坊", "白酒"),
    # 中药
    ("同仁堂", "中药"), ("云南白药", "中药"), ("片仔癀", "中药"), ("东阿阿胶", "中药"),
    ("以岭", "中药"), ("康缘", "中药"), ("太极集团", "中药"),
    # CXO
    ("药明康德", "医药研发外包（CXO）"), ("药明生物", "医药研发外包（CXO）"),
    ("康龙化成", "医药研发外包（CXO）"), ("泰格医药", "医药研发外包（CXO）"),
    ("凯莱英", "医药研发外包（CXO）"), ("昭衍新药", "医药研发外包（CXO）"),
    # 半导体
    ("中芯国际", "半导体晶圆代工"), ("华虹", "半导体晶圆代工"),
    ("兆易创新", "半导体芯片设计"), ("韦尔股份", "半导体芯片设计"), ("卓胜微", "半导体芯片设计"),
    ("澜起科技", "半导体芯片设计"), ("紫光国微", "半导体芯片设计"), ("圣邦股份", "半导体芯片设计"),
    # 建筑/园林/工程
    ("美丽生态", "土木工程建筑"), ("东方园林", "土木工程建筑"), ("岭南股份", "土木工程建筑"),
    ("诚邦", "土木工程建筑"), ("花王股份", "土木工程建筑"),
    # 环保
    ("仕净科技", "环保工程设备"), ("仕净环保", "环保工程设备"), ("龙净环保", "环保工程设备"),
    ("菲达环保", "环保工程设备"), ("久吾高科", "环保工程设备"),
]


def _load_industry_keywords() -> list:
    rules = []
    if INDUSTRY_KEYWORDS_CSV.exists():
        try:
            df = pd.read_csv(INDUSTRY_KEYWORDS_CSV, dtype=str).fillna("")
            for _, r in df.iterrows():
                if r.get("关键词", "").strip():
                    rules.append((r["关键词"].strip(), r["所属行业"].strip()))
        except Exception:
            pass
    rules.extend(DEFAULT_IND_KEYWORDS)
    rules.sort(key=lambda kv: -len(kv[0]))
    return rules


def guess_industry(company: str) -> str:
    for kw, ind in _load_industry_keywords():
        if kw in company or company in kw:
            return ind
    return ""


def _read_meta_csv() -> pd.DataFrame:
    if COMPANY_META_CSV.exists():
        df = pd.read_csv(COMPANY_META_CSV, dtype=str).fillna("")
    else:
        df = pd.DataFrame(columns=["公司名称", "股票代码", "所属行业"])
    for col in ("公司名称", "股票代码", "所属行业"):
        if col not in df.columns:
            df[col] = ""
    return df


def _match_row(df: pd.DataFrame, company: str):
    hit = df[df["公司名称"].str.strip() == company.strip()]
    if hit.empty:
        hit = df[df["公司名称"].apply(
            lambda x: x.strip() and (x.strip() in company or company in x.strip()))]
    return hit.iloc[0] if not hit.empty else None


def remember_meta(company: str, code: str, industry: str):
    if not code and not industry:
        return
    with _WRITE_LOCK:                       # 多线程互斥：读-改-写整个CSV
        df = _read_meta_csv()
        idx = None
        for i, r in df.iterrows():
            name = str(r["公司名称"]).strip()
            if name and (name == company.strip() or name in company or company in name):
                idx = i
                break
        if idx is not None:
            if not str(df.at[idx, "股票代码"]).strip():
                df.at[idx, "股票代码"] = code
            if not str(df.at[idx, "所属行业"]).strip():
                df.at[idx, "所属行业"] = industry
        else:
            df.loc[len(df)] = {"公司名称": company, "股票代码": code, "所属行业": industry}
        COMPANY_META_CSV.parent.mkdir(exist_ok=True)
        df.to_csv(COMPANY_META_CSV, index=False, encoding="utf-8-sig")


def resolve_meta(company: str, pdf_path=None, log=print) -> dict:
    with _WRITE_LOCK:                       # 读也加锁，避免读到写了一半的状态
        df = _read_meta_csv()
        row = _match_row(df, company)
    code = str(row["股票代码"]).strip() if row is not None else ""
    industry = str(row["所属行业"]).strip() if row is not None else ""

    src = []
    if not code and pdf_path is not None and Path(pdf_path).exists():
        code = extract_code_from_pdf(pdf_path)
        if code:
            src.append(f"代码来自PDF封面({code})")
    if not industry:
        industry = guess_industry(company)
        if industry:
            src.append(f"行业按关键词推断({industry})")

    if src:
        remember_meta(company, code, industry)
        log(f"[meta] {company}：{'；'.join(src)}（已回写 company_meta.csv）")
    else:
        log(f"[meta] {company}：股票代码={code or '未识别'}，所属行业={industry or '未识别'}（CSV命中）")
    return {"股票代码": code, "所属行业": industry}
