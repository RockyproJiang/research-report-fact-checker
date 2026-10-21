
from pathlib import Path

import pandas as pd

import extract_financial_datav2 as v2
import config
from config import FILES_DIR


def _parse_one_pdf(pdf_path_str: str):
    """子进程入口：解析单份PDF，只返回可pickle的纯数据（不写文件、不打日志）"""
    pdf_path = Path(pdf_path_str)          # 还原为Path：v2内核依赖Path属性
    import extract_key_info as ki         # 子进程内导入（Windows spawn模式要求）
    rows, audits, errors = [], [], []
    try:
        rows, file_audits = v2.extract_from_pdf(pdf_path)
        audits.extend(file_audits)
    except Exception as exc:
        errors.append({"文件名": pdf_path.name,
                       "错误类型": type(exc).__name__, "错误信息": str(exc)})
    key_info = {}
    try:
        key_info = ki.extract_key_info(pdf_path)
    except Exception as exc:
        key_info = {"_错误": str(exc)}
    return rows, audits, errors, key_info


def iter_parse(log):
    """多进程解析全部PDF；每份解析完成并落盘后立刻 yield (stem, raw_df, key_info)。
    主流程边接收边把报告送入下游，实现解析与报告生成的时间重叠。"""
    if not FILES_DIR.is_dir():
        raise SystemExit(f"未找到PDF目录：{FILES_DIR}")
    pdfs = sorted(FILES_DIR.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"{FILES_DIR} 中没有PDF文件")

    n_workers = max(1, int(getattr(config, "PARALLEL_PDF", 1)))
    log(f"[step1] 共 {len(pdfs)} 份PDF，并行解析进程数：{n_workers}")

    def _persist(stem, rows, audits, errors, key_info):
        """主进程落盘单份报告的step1产物，返回 (raw_df, key_info)"""
        paths = config.make_report_paths(stem)
        paths.mkdir()
        df = pd.DataFrame(rows, columns=v2.OUTPUT_FIELDS)
        df.to_csv(paths.RAW_CSV, index=False, encoding="utf-8-sig")
        pd.DataFrame(audits).to_csv(paths.AUDIT_CSV, index=False, encoding="utf-8-sig")
        pd.DataFrame(errors).to_csv(paths.ERROR_CSV, index=False, encoding="utf-8-sig")
        ki_rows = []
        for k, val in key_info.items():
            if isinstance(val, list):
                val = " | ".join(str(x) for x in val)
            ki_rows.append({"字段": k, "值": str(val)})
        pd.DataFrame(ki_rows).to_csv(paths.KEY_INFO_CSV, index=False, encoding="utf-8-sig")
        err_note = f"；⚠️错误: {errors[0]['错误类型']}: {errors[0]['错误信息'][:80]}" if errors else ""
        log(f"[step1] {stem}：原始提取 {len(df)} 行；关键信息 {len(ki_rows)} 条{err_note}")
        return df, key_info

    if n_workers == 1:
        for pdf in pdfs:
            log(f"[step1] 解析 {pdf.name}")
            rows, audits, errors, key_info = _parse_one_pdf(str(pdf))
            df, key_info = _persist(pdf.stem, rows, audits, errors, key_info)
            yield pdf.stem, df, key_info
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            futures = {ex.submit(_parse_one_pdf, str(pdf)): pdf for pdf in pdfs}
            done = 0
            for fut in as_completed(futures):
                pdf = futures[fut]
                try:
                    rows, audits, errors, key_info = fut.result()
                except Exception as exc:
                    rows, audits, errors, key_info = [], [], \
                        [{"文件名": pdf.name, "错误类型": type(exc).__name__,
                          "错误信息": str(exc)}], {}
                done += 1
                log(f"[step1] 解析完成 {done}/{len(pdfs)}：{pdf.name}")
                df, key_info = _persist(pdf.stem, rows, audits, errors, key_info)
                yield pdf.stem, df, key_info


def run_all(log) -> dict:
    """兼容旧入口：一次性解析全部并返回字典（不流水线）"""
    result = {}
    for stem, df, key_info in iter_parse(log):
        result[stem] = {"raw": df, "key_info": key_info}
    return result


if __name__ == "__main__":
    run_all(lambda m: print(m))
