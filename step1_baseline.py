import pandas as pd
from pathlib import Path
import extract_financial_datav2 as v2
import extract_key_info as ki
import config
from config import FILES_DIR

def run_one(pdf: Path, log=print, paths=None) -> dict:
    """解析单个财报PDF（第一信源），返回 {raw, key_info} 并写出 01_* 文件。
    paths 可指定输出位置（如 output\\_基准库_<公司：报告期>\\）；默认按 PDF 文件名。"""
    stem = pdf.stem
    log(f"[step1] 解析基准财报 {pdf.name}")
    paths = paths or config.make_report_paths(stem)
    paths.mkdir()
    rows, audits, errors = [], [], []
    try:
        rows, file_audits = v2.extract_from_pdf(pdf)
        audits.extend(file_audits)
    except Exception as exc:
        errors.append({"文件名": pdf.name, "错误类型": type(exc).__name__,
                       "错误信息": str(exc)})
        log(f"[step1] 基准提取失败 {pdf.name}: {exc}")
    df = pd.DataFrame(rows, columns=v2.OUTPUT_FIELDS)
    df.to_csv(paths.RAW_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(audits).to_csv(paths.AUDIT_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(errors).to_csv(paths.ERROR_CSV, index=False, encoding="utf-8-sig")
    key_info = {}
    try:
        key_info = ki.extract_key_info(pdf)
    except Exception as exc:
        log(f"[step1] 关键信息提取失败 {pdf.name}: {exc}")
    ki_rows = [{"字段": k, "值": " | ".join(map(str, v)) if isinstance(v, list) else str(v)}
               for k, v in key_info.items()]
    pd.DataFrame(ki_rows).to_csv(paths.KEY_INFO_CSV, index=False, encoding="utf-8-sig")
    log(f"[step1] {stem}：基准提取 {len(df)} 行；关键信息 {len(ki_rows)} 条")
    return {"raw": df, "key_info": key_info}


def run_all(log) -> dict:
    if not FILES_DIR.is_dir():
        raise SystemExit(f"未找到原始财报目录：{FILES_DIR}")
    pdfs = sorted(FILES_DIR.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"{FILES_DIR} 中没有PDF文件（核查基准缺失）")
    result = {}
    for pdf in pdfs:
        result[pdf.stem] = run_one(pdf, log)
    return result
