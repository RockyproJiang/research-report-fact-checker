
import os
import shutil
import sys
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

import config
import company_meta
import step1_extract
import step2_standardize as s2
import step3_indicators as s3
import step4_rules as s4
import step5_llm_report as s5
import step6_structured as s6
from config import OUTPUT_DIR


def _clean_output(output_dir, log):
    if os.environ.get("KEEP_OUTPUT") == "1":
        log("[clean] KEEP_OUTPUT=1，跳过清空 output 目录")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    removed_dirs, removed_files, failed = 0, 0, 0
    for entry in output_dir.iterdir():
        try:
            if entry.is_dir():
                shutil.rmtree(entry); removed_dirs += 1
            else:
                entry.unlink(); removed_files += 1
        except Exception as exc:
            failed += 1
            log(f"[clean] 删除失败：{entry}（{type(exc).__name__}: {exc}）")
    log(f"[clean] 已清空 output 目录：删除 {removed_dirs} 个子目录、{removed_files} 个文件"
        + (f"；{failed} 项删除失败" if failed else ""))


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def _refine_key_info(key_info: dict, d: dict, log, stem: str) -> dict:
    """关键信息精修：精确值覆盖 + 缺失互相回填 + 口径一致性提示。"""
    key_info = dict(key_info or {})
    pl = d.get("利润表", {})
    cf = d.get("现金流量表", {})

    # ① 归母净利润：合并利润表精确值覆盖摘要页四舍五入值
    stmt_ni = pl.get("归属于母公司净利润_本期")
    if stmt_ni is not None:
        old = _f(key_info.get("归母净利润"))
        key_info["归母净利润"] = f"{stmt_ni:.2f}"
        note = key_info.get("归母净利润_备注", "")
        key_info["归母净利润_备注"] = (note + "；取合并利润表精确值").strip("；")
        if old is not None and abs(old - stmt_ni) / abs(stmt_ni) > 0.001:
            log(f"[refine] {stem}：归母净利润由 {old:,.2f} 修正为利润表精确值 {stmt_ni:,.2f}")

    # ② 分红付息现金缺失时从现金流量表补
    if not key_info.get("分红付息现金"):
        v = cf.get("分红付息现金")
        if v is not None:
            key_info["分红付息现金"] = f"{v:.2f}"
            log(f"[refine] {stem}：分红付息现金由现金流量表补齐 {v:,.2f}")

    # ③ 扣非 / 非经常性合计 缺失时互相推算回填（标注推算）
    ni, kf, nr = _f(key_info.get("归母净利润")), _f(key_info.get("扣非归母净利润")), \
        _f(key_info.get("非经常性损益合计"))
    if kf is None and None not in (ni, nr):
        key_info["扣非归母净利润"] = f"{ni - nr:.2f}"
        key_info["扣非归母净利润_备注"] = "推算值（归母-非经常性合计），请人工核对"
        log(f"[refine] {stem}：扣非由归母-非经常推算 = {ni - nr:,.2f}")
    elif nr is None and None not in (ni, kf):
        key_info["非经常性损益合计"] = f"{ni - kf:.2f}"
        log(f"[refine] {stem}：非经常性合计由归母-扣非推算 = {ni - kf:,.2f}")

    # ④ 三者齐全但互斥：归母-扣非 应约等于 非经常合计，否则提示口径差
    ni, kf, nr = _f(key_info.get("归母净利润")), _f(key_info.get("扣非归母净利润")), \
        _f(key_info.get("非经常性损益合计"))
    if None not in (ni, kf, nr) and abs(nr) > 1.0:
        if abs((ni - kf) - nr) / abs(nr) > 0.2:
            note = key_info.get("归母净利润_备注", "")
            key_info["归母净利润_备注"] = (note + "；注意：归母-扣非≠非经常性合计"
                "（可能为税前/少数股东/其他权益工具口径差），请人工核对").strip("；")
            log(f"[refine] {stem}：归母-扣非 与 非经常性合计 口径不一致，已标注")
    return key_info


def process_one(stem, raw, key_info, std, comp, fname, comp_reports,
                cont_map, log) -> str:
    paths = config.make_report_paths(stem)
    log(f"===== 开始处理：{stem} =====")

    if std is None or std.empty:
        log(f"[跳过] {stem}：标准化结果为空，请检查 01_原始提取.csv 与 mapping/subject_map.csv")
        return "标准化失败"

    nonrec_df = s2.extract_nonrec(raw)
    item = comp_reports[fname]
    d, evid = item["d"], item["evid"]
    period = f"{item['year']}年{item['period']}"

    # 源文件内部一致性修复（仕净科技：负债合计≠分项之和 → 修正总负债）
    reconcile_notes = s3.reconcile_report(d)
    for note in reconcile_notes:
        log(f"[reconcile] {stem}：{note[:100]}...")

    # 关键信息精修（精确值覆盖 + 缺失回填 + 口径提示）
    key_info = _refine_key_info(key_info, d, log, stem)

    singles = s3.derive_singles({comp: comp_reports}, comp).get(fname, {})
    single = singles.get("single")
    log(f"[step3] 计算指标：{comp} {period}"
        f"（单季口径{'可用' if single else '缺失（年报-only时Q4单季不可拆，属正常）'}）")

    ind = s3.compute_indicators(d, single=single,
                                ring_single=singles.get("ring_single"),
                                py_single=singles.get("py_single"))
    hits = s3.check_indicators(ind)
    ind["_threshold_hits"] = hits
    gouji = s3.check_gouji(d, ind, key_info)

    ring = s4.ring_from_singles(singles)
    continuity = cont_map.get(fname, [])
    alerts = s4.scan(d, ind, gouji, continuity, nonrec_df, ring,
                     key_info=key_info, reconcile_notes=reconcile_notes)
    n_fail = sum(1 for r in gouji if r["勾稽校验结论"] == "不通过")
    log(f"[step4] {stem}：异常 {len(alerts)} 项；勾稽 {len(gouji)} 项中不通过 {n_fail} 项；"
        f"跨报告衔接记录 {len(continuity)} 条")

    all_ind = [{"公司": comp, "报告期": period, "指标": k,
                "数值": round(v, 6) if isinstance(v, float) else v}
               for k, v in ind.items() if not k.startswith("_")]
    pd.DataFrame(all_ind).to_csv(paths.IND_CSV, index=False, encoding="utf-8-sig")

    evidence = {"科目证据": evid, "环比": ring, "单季口径": single,
                "源文件一致性": reconcile_notes,
                "指标": {k: v for k, v in ind.items() if not k.startswith("_")},
                "非经常性损益": nonrec_df.to_dict("records") if len(nonrec_df) else [],
                "关键信息": key_info,
                "跨报告衔接": continuity}
    paths.EVIDENCE_JSON.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")

    pdf_path = config.FILES_DIR / f"{stem}.pdf"
    meta = company_meta.resolve_meta(comp, pdf_path, log)

    # 行业豁免（在写异常清单之前完成，保证 04 与 07 口径一致）
    alerts, exempt_log = s6.apply_industry_exemption(alerts, meta.get("所属行业", ""))
    if exempt_log:
        log(f"[exempt] {stem}：行业豁免 {len(exempt_log)} 项：" +
            "；".join(f"{e['项目']}({e['原始等级']}→预警)" for e in exempt_log))
    risk = s6.overall_risk(alerts)
    log(f"[risk] {comp} 整体风险等级：{risk[0]}")

    # 04_异常清单：豁免后写出，带原始等级/权重列
    all_alerts = []
    for a in alerts:
        row = {"公司": comp, "报告期": period}
        row.update(a)
        all_alerts.append(row)
    alert_cols = ["公司", "报告期", "类别", "项目", "数值", "等级", "权重", "说明",
                  "原始等级", "原始权重", "行业豁免"]
    pd.DataFrame(all_alerts).reindex(columns=alert_cols).to_csv(
        paths.ALERT_CSV, index=False, encoding="utf-8-sig")

    s5.generate(comp, period, ind, alerts, gouji, evid, nonrec_df, d, meta, risk,
                paths, exempt_log, key_info=key_info)

    pkg = s6.build(comp, period, meta, ind, hits, gouji, alerts, evid, paths,
                   exempt_log, key_info=key_info)
    log(f"[step6] {stem}：结构化基准包已生成（勾稽{len(pkg['勾稽校验结果'])}项/"
        f"指标{len(pkg['指标计算结果'])}项/"
        f"异常{sum(1 for x in pkg['指标异常结果'] if x['异常等级'] != '无')}项，"
        f"整体风险：{pkg['汇总风险']['整体风险等级']}）")
    return risk[0]


def main():
    log_lines = []
    _log_lock = threading.Lock()

    def log(msg):
        with _log_lock:
            print(msg, flush=True)
            log_lines.append(msg)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _clean_output(OUTPUT_DIR, log)

    summary = {}
    try:
        parsed = []
        try:
            for stem, raw, key_info in step1_extract.iter_parse(log):
                parsed.append((stem, raw, key_info))
        except SystemExit as exc:
            log(f"[中止] {exc}")
            (OUTPUT_DIR / "run.log").write_text("\n".join(log_lines), encoding="utf-8")
            return
        if not parsed:
            log("[中止] 没有成功解析的PDF")
            (OUTPUT_DIR / "run.log").write_text("\n".join(log_lines), encoding="utf-8")
            return

        n_workers = max(1, int(getattr(config, "PARALLEL_REPORTS", 1)))

        log(f"[main] 阶段2：标准化 {len(parsed)} 份报告并全局组装（同比/环比/口径变化的数据基础）")
        std_frames = {}

        def _std(task):
            stem, raw = task
            return stem, s2.run(raw, log, config.make_report_paths(stem))

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futs = [pool.submit(_std, (s, r)) for s, r, _ in parsed]
            for fut in as_completed(futs):
                try:
                    stem, std = fut.result()
                    if std is not None and not std.empty:
                        std_frames[stem] = std
                except Exception:
                    log(f"[标准化失败]\n" + traceback.format_exc())

        if not std_frames:
            log("[中止] 全部报告标准化结果为空，请检查 01_原始提取.csv 与 subject_map.csv")
            (OUTPUT_DIR / "run.log").write_text("\n".join(log_lines), encoding="utf-8")
            return

        big = pd.concat(std_frames.values(), ignore_index=True)
        companies, continuity = s2.assemble(big)
        log(f"[main] 全局组装完成：{len(companies)} 家公司，"
            f"跨报告衔接差异 {len(continuity)} 条")

        cont_by_comp = {}
        for c in continuity:
            cont_by_comp.setdefault(c["公司"], {}).setdefault(c["当前报告"], []).append(c)

        stem2cf = {}
        for comp, reports in companies.items():
            for fname in reports:
                for stem, _, _ in parsed:
                    if fname == stem or fname == stem + ".pdf" or Path(fname).stem == stem:
                        stem2cf[stem] = (comp, fname)
                        break

        log(f"[main] 阶段3：报告并行线程数 {n_workers}")
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futs = {}
            for stem, raw, key_info in parsed:
                if stem not in stem2cf:
                    log(f"[跳过] {stem}：标准化为空")
                    summary[stem] = "标准化失败"
                    continue
                comp, fname = stem2cf[stem]
                futs[pool.submit(process_one, stem, raw, key_info,
                                 std_frames[stem], comp, fname,
                                 companies[comp],
                                 cont_by_comp.get(comp, {}), log)] = stem
            for fut in as_completed(futs):
                stem = futs[fut]
                try:
                    summary[stem] = fut.result()
                except Exception:
                    log(f"[报告失败] {stem}\n" + traceback.format_exc())
                    summary[stem] = "处理失败"

        stems_in_order = sorted(p.stem for p in config.FILES_DIR.glob("*.pdf"))
        rows = [{"报告名": s, "整体风险等级": summary.get(s, "未处理")}
                for s in stems_in_order]
        pd.DataFrame(rows).to_csv(OUTPUT_DIR / "00_批处理汇总.csv",
                                  index=False, encoding="utf-8-sig")
        ok = sum(1 for s in summary.values()
                 if s not in ("处理失败", "标准化失败"))
        log(f"[done] 共处理 {len(summary)} 份报告：成功 {ok} 份；"
            f"汇总见 00_批处理汇总.csv")
    except Exception:
        log("流程异常：\n" + traceback.format_exc())
    finally:
        (OUTPUT_DIR / "run.log").write_text("\n".join(log_lines), encoding="utf-8")


if __name__ == "__main__":
    main()