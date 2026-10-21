

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import pdfplumber
except ImportError:
    raise SystemExit("缺少 pdfplumber，请先执行: python -m pip install pdfplumber")


STATEMENT_ORDER = [
    ("合并资产负债表", ["合并资产负债表"]),
    ("母公司资产负债表", ["母公司资产负债表"]),
    ("合并利润表", ["合并利润表"]),
    ("母公司利润表", ["母公司利润表"]),
    ("合并现金流量表", ["合并现金流量表"]),
    ("母公司现金流量表", ["母公司现金流量表"]),
    ("合并所有者权益变动表", ["合并所有者权益变动表", "合并股东权益变动表"]),
    ("母公司所有者权益变动表", ["母公司所有者权益变动表", "母公司股东权益变动表"]),
]

TARGET_STATEMENTS = ("合并资产负债表", "合并利润表", "合并现金流量表")
UNIT_RE = re.compile(r"单位[:：]\s*(千元|万元|百万元|亿元|元)")
NEW_ITEM_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+、"
    r"|（[一二三四五六七八九十]+）"
    r"|\([一二三四五六七八九十]+\)"
    r"|\d+[.．、]"
    r"|（\d+）|\(\d+\)"
    r"|其中[:：]|减[:：]|加[:：]|减\s|加\s)"
)

OUTPUT_FIELDS = [
    "文件名",
    "公司名称",
    "报表",
    "项目序号",
    "项目",
    "本期金额",
    "上期金额",
    "单位",
    "金额状态",
    "PDF页码",
    "表格序号",
    "表内行号",
    "坐标x0",
    "坐标top",
    "坐标x1",
    "坐标bottom",
    "数据位置",
]


@dataclass(frozen=True)
class StatementStart:
    label: str
    page_index: int
    title_top: float
    title_bottom: float


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", "").replace("\u00a0", " ").strip()


def parse_number(value: object) -> float | None:
    text = clean_text(value).replace(",", "").replace("，", "").replace(" ", "")
    if not text or text in ("-", "－", "—", "/", "无", "不适用", "N/A", "--"):
        return None
    negative = False
    if (text.startswith("(") and text.endswith(")")) or (
        text.startswith("（") and text.endswith("）")
    ):
        negative = True
        text = text[1:-1]
    if text.startswith(("-", "－", "−")):
        negative = True
        text = text[1:]
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def normalize_amount(value: object) -> str:
    if parse_number(value) is None:
        return ""
    text = clean_text(value).replace(",", "").replace("，", "").replace(" ", "")
    if (text.startswith("(") and text.endswith(")")) or (
        text.startswith("（") and text.endswith("）")
    ):
        return "-" + text[1:-1]
    if text.startswith(("－", "−")):
        return "-" + text[1:]
    return text


def page_text(page: object) -> str:
    try:
        return page.extract_text() or ""
    except Exception:
        return ""


def find_title_bounds(page: object, aliases: Iterable[str]) -> tuple[float, float] | None:
    for alias in aliases:
        try:
            matches = page.search(re.escape(alias), regex=True) or []
        except Exception:
            matches = []
        if matches:
            match = min(matches, key=lambda item: (float(item["top"]), float(item["x0"])))
            return float(match["top"]), float(match["bottom"])
    return None


def find_statement_starts(pdf: object) -> list[StatementStart]:
    found: dict[str, StatementStart] = {}
    for page_index, page in enumerate(pdf.pages):
        text = page_text(page)
        if not text or not UNIT_RE.search(text):
            continue
        for label, aliases in STATEMENT_ORDER:
            if label in found or not any(alias in text for alias in aliases):
                continue
            bounds = find_title_bounds(page, aliases)
            if bounds is None:
                continue
            found[label] = StatementStart(label, page_index, bounds[0], bounds[1])
    return [found[label] for label, _ in STATEMENT_ORDER if label in found]


def detect_unit(pdf: object, start_page: int) -> str:
    for page_index in range(start_page, min(start_page + 2, len(pdf.pages))):
        match = UNIT_RE.search(page_text(pdf.pages[page_index]))
        if match:
            return match.group(1)
    return "元"


def get_company_name(pdf: object, start_page: int, filename: str) -> str:
    for page_index in range(start_page, min(start_page + 3, len(pdf.pages))):
        match = re.search(r"编制单位[:：]\s*([^\n]+)", page_text(pdf.pages[page_index]))
        if match:
            return match.group(1).strip()
    return filename.split("：", 1)[0] if "：" in filename else Path(filename).stem


def row_bbox(row: object) -> tuple[float, float, float, float] | None:
    cells = [cell for cell in getattr(row, "cells", []) if cell]
    if not cells:
        return None
    return (
        min(float(cell[0]) for cell in cells),
        min(float(cell[1]) for cell in cells),
        max(float(cell[2]) for cell in cells),
        max(float(cell[3]) for cell in cells),
    )


def positioned_tables(
    pdf: object,
    start: StatementStart,
    next_start: StatementStart | None,
) -> list[dict[str, object]]:
    end_page = next_start.page_index if next_start else len(pdf.pages) - 1
    result: list[dict[str, object]] = []
    for page_index in range(start.page_index, end_page + 1):
        page = pdf.pages[page_index]
        lower_bound = start.title_bottom if page_index == start.page_index else 0.0
        upper_bound = (
            next_start.title_top
            if next_start is not None and page_index == next_start.page_index
            else float(page.height)
        )
        try:
            tables = page.find_tables() or []
        except Exception:
            tables = []
        for table_index, table in enumerate(tables, start=1):
            top = float(table.bbox[1])
            bottom = float(table.bbox[3])
            if bottom <= lower_bound or top >= upper_bound:
                continue
            data = table.extract() or []
            table_rows = list(getattr(table, "rows", []))
            result.append(
                {
                    "page_index": page_index,
                    "table_index": table_index,
                    "data": data,
                    "row_bboxes": [
                        row_bbox(table_rows[index]) if index < len(table_rows) else None
                        for index in range(len(data))
                    ],
                }
            )
    return result


def is_header(row: list[object]) -> bool:
    return any(clean_text(cell) == "项目" for cell in row)


def looks_like_tail(name: str) -> bool:
    if not name or name.endswith("：") or NEW_ITEM_RE.match(name):
        return False
    if name.startswith("号"):
        return True
    return name.startswith(("（", "(")) and name.endswith(("）", ")", "合计"))


def amount_status(current: str, prior: str) -> str:
    if current and prior:
        return "两期均有金额"
    if current:
        return "仅本期有金额"
    if prior:
        return "仅上期有金额"
    return "财报原文两期均为空"


def parse_positioned_statement(
    tables: list[dict[str, object]], statement: str, unit: str
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    name_col: int | None = None
    note_col: int | None = None
    current_col: int | None = None
    prior_col: int | None = None
    started = False

    for table in tables:
        data = table["data"]
        assert isinstance(data, list)
        row_bboxes = table["row_bboxes"]
        assert isinstance(row_bboxes, list)
        for row_index, row in enumerate(data, start=1):
            if not row or not any(clean_text(cell) for cell in row):
                continue
            if is_header(row):
                cells = [clean_text(cell) for cell in row]
                name_col = cells.index("项目")
                note_col = next(
                    (index for index, cell in enumerate(cells)
                     if cell.startswith(("附注", "注释"))), None   # v2: 兼容"注释"表头（中芯国际等）
                )
                # 优先按本张表实际出现的数字列定位金额。部分年报表头使用
                # 合并单元格，表头文字位于第5/8列，而数据实际在第4/7列。
                max_columns = max((len(item) for item in data if item), default=len(cells))
                numeric_counts = [0] * max_columns
                for candidate_row in data:
                    if not candidate_row or is_header(candidate_row):
                        continue
                    for candidate_index, candidate_value in enumerate(candidate_row):
                        if candidate_index in {name_col, note_col}:
                            continue
                        if parse_number(candidate_value) is not None:
                            numeric_counts[candidate_index] += 1
                detected_cols = [
                    index for index, count in enumerate(numeric_counts)
                    if count > 0 and index > name_col   # v2: 金额列限定在项目列右侧，防序号列混入
                ]
                if not detected_cols:   # 异常版式兜底
                    detected_cols = [
                        index for index, count in enumerate(numeric_counts) if count > 0
                    ]
                value_cols = detected_cols or [
                    index
                    for index in range(len(cells))
                    if index not in {name_col, note_col} and cells[index]
                ]
                if len(value_cols) >= 2:
                    current_col, prior_col = value_cols[0], value_cols[1]
                else:
                    fallback = [
                        index
                        for index in range(len(cells))
                        if index not in {name_col, note_col}
                    ]
                    if len(fallback) >= 2:
                        current_col, prior_col = fallback[0], fallback[1]
                started = True
                continue
            if not started or name_col is None or current_col is None or prior_col is None:
                continue

            name = clean_text(row[name_col]) if name_col < len(row) else ""
            current_value = row[current_col] if current_col < len(row) else None
            prior_value = None
            # 某些 PDF 在跨页中途会插入一个全空的伪列，使上期金额从第4列
            # 临时移到第5列。固定本期列，但从上期列开始向右寻找首个金额。
            for candidate_col in range(prior_col, len(row)):
                if parse_number(row[candidate_col]) is not None:
                    prior_value = row[candidate_col]
                    break
            current = normalize_amount(current_value)
            prior = normalize_amount(prior_value)
            if not name and not current and not prior:
                continue
            if not name:
                name = "未识别项目名称"

            bbox = row_bboxes[row_index - 1] if row_index - 1 < len(row_bboxes) else None
            page_number = int(table["page_index"]) + 1
            table_number = int(table["table_index"])
            location = f"PDF第{page_number}页｜{statement}｜表{table_number}第{row_index}行"
            record: dict[str, object] = {
                "报表": statement,
                "项目": name,
                "本期金额": current,
                "上期金额": prior,
                "单位": unit,
                "金额状态": amount_status(current, prior),
                "PDF页码": page_number,
                "表格序号": table_number,
                "表内行号": row_index,
                "坐标x0": "",
                "坐标top": "",
                "坐标x1": "",
                "坐标bottom": "",
                "数据位置": location,
            }
            if bbox:
                record.update(
                    {
                        "坐标x0": f"{bbox[0]:.2f}",
                        "坐标top": f"{bbox[1]:.2f}",
                        "坐标x1": f"{bbox[2]:.2f}",
                        "坐标bottom": f"{bbox[3]:.2f}",
                    }
                )

            if looks_like_tail(name) and not current and not prior and rows:
                previous = rows[-1]
                if not str(previous["项目"]).endswith("："):
                    previous["项目"] = str(previous["项目"]) + name
                    previous["数据位置"] = str(previous["数据位置"]) + f"；续行见PDF第{page_number}页"
                    continue
            rows.append(record)

    for item_index, record in enumerate(rows, start=1):
        record["项目序号"] = item_index
    return rows


def extract_from_pdf(pdf_path: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    filename = pdf_path.name
    output_rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []
    with pdfplumber.open(pdf_path) as pdf:
        starts = find_statement_starts(pdf)
        start_by_label = {item.label: item for item in starts}
        company = filename.split("：", 1)[0] if "：" in filename else pdf_path.stem
        for label in TARGET_STATEMENTS:
            if label in start_by_label:
                company = get_company_name(pdf, start_by_label[label].page_index, filename)
                break

        for label in TARGET_STATEMENTS:
            start = start_by_label.get(label)
            if start is None:
                audit_rows.append(
                    {
                        "文件名": filename,
                        "公司名称": company,
                        "报表": label,
                        "状态": "未找到报表",
                        "提取行数": 0,
                        "有金额行数": 0,
                        "原文空金额行数": 0,
                        "起始PDF页码": "",
                    }
                )
                continue
            start_position = starts.index(start)
            next_start = starts[start_position + 1] if start_position + 1 < len(starts) else None
            unit = detect_unit(pdf, start.page_index)
            tables = positioned_tables(pdf, start, next_start)
            statement_rows = parse_positioned_statement(tables, label, unit)
            for row in statement_rows:
                row["文件名"] = filename
                row["公司名称"] = company
            output_rows.extend(statement_rows)
            empty_count = sum(
                row["金额状态"] == "财报原文两期均为空" for row in statement_rows
            )
            if not statement_rows:
                statement_status = "找到标题但未提取到行"
            elif empty_count == len(statement_rows):
                statement_status = "疑似识别失败：全部金额为空"
            else:
                statement_status = "已提取"
            audit_rows.append(
                {
                    "文件名": filename,
                    "公司名称": company,
                    "报表": label,
                    "状态": statement_status,
                    "提取行数": len(statement_rows),
                    "有金额行数": len(statement_rows) - empty_count,
                    "原文空金额行数": empty_count,
                    "起始PDF页码": start.page_index + 1,
                }
            )
    return output_rows, audit_rows


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="提取合并财务报表并记录 PDF 数据位置")
    parser.add_argument("--input-dir", type=Path, help="包含 PDF 的文件夹")
    parser.add_argument("--file", type=Path, help="仅处理一份指定 PDF")
    parser.add_argument("--output", type=Path, help="结果 CSV 路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent
    input_dir = args.input_dir or (base_dir / "files")
    output_path = args.output or (base_dir / "财务数据提取结果_v2.csv")
    audit_path = output_path.with_name(output_path.stem + "_审计汇总.csv")
    error_path = output_path.with_name(output_path.stem + "_错误日志.csv")

    if args.file:
        pdf_files = [args.file]
    else:
        if not input_dir.is_dir():
            raise SystemExit(f"未找到文件夹: {input_dir}")
        pdf_files = sorted(input_dir.glob("*.pdf"))
    if not pdf_files:
        raise SystemExit("没有找到 PDF 文件")

    all_rows: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for pdf_path in pdf_files:
        print(f"解析: {pdf_path.name}")
        try:
            rows, file_audits = extract_from_pdf(pdf_path)
            all_rows.extend(rows)
            audits.extend(file_audits)
        except Exception as exc:
            errors.append({"文件名": pdf_path.name, "错误类型": type(exc).__name__, "错误信息": str(exc)})
            print(f"失败: {pdf_path.name}: {exc}", file=sys.stderr)

    write_csv(output_path, all_rows, OUTPUT_FIELDS)
    audit_fields = [
        "文件名",
        "公司名称",
        "报表",
        "状态",
        "提取行数",
        "有金额行数",
        "原文空金额行数",
        "起始PDF页码",
    ]
    write_csv(audit_path, audits, audit_fields)
    write_csv(error_path, errors, ["文件名", "错误类型", "错误信息"])

    counts = Counter(row["报表"] for row in all_rows)
    print(f"已保存: {output_path}，共 {len(all_rows)} 行")
    for statement in TARGET_STATEMENTS:
        print(f"{statement}: {counts[statement]} 行")
    print(f"审计汇总: {audit_path}")
    print(f"错误日志: {error_path}（{len(errors)} 条）")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())