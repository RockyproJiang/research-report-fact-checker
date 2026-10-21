import os
import re
from dataclasses import dataclass
from pathlib import Path

BASE_DIR   = Path(__file__).resolve().parent

# 可选：本地 .env 支持（python -m pip install python-dotenv）
# 未安装时静默跳过，直接使用系统环境变量。
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR.parent / ".env")   # 兼容放在项目根的 .env
    load_dotenv(BASE_DIR / ".env")          # 也兼容放在本子项目下的 .env
except ImportError:
    pass

FILES_DIR  = BASE_DIR / "files"
OUTPUT_DIR = BASE_DIR / "output"
MAPPING_CSV = BASE_DIR / "mapping" / "subject_map.csv"

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')


def make_report_paths(pdf_stem: str) -> "ReportPaths":
    name = _INVALID_CHARS.sub("＿", pdf_stem.strip()).rstrip(". ")
    return ReportPaths(name or "未命名报告")


@dataclass
class ReportPaths:
    report_name: str

    def __post_init__(self):
        d = OUTPUT_DIR / self.report_name
        self.dir = d
        self.RAW_CSV      = d / "01_原始提取.csv"
        self.AUDIT_CSV    = d / "01_审计汇总.csv"
        self.ERROR_CSV    = d / "01_错误日志.csv"
        self.KEY_INFO_CSV = d / "01_关键信息.csv"
        self.STD_CSV      = d / "02_标准化数据.csv"
        self.IND_CSV      = d / "03_指标结果.csv"
        self.ALERT_CSV    = d / "04_异常清单.csv"
        self.EVIDENCE_JSON = d / "05_证据链.json"
        self.REPORT_MD    = d / "06_分析报告.md"
        self.BENCH_JSON   = d / "07_结构化基准包.json"
        self.BASE_CSV     = d / "07-1_基础信息.csv"
        self.IND_CALC_CSV = d / "07-2_指标计算结果.csv"
        self.IND_ABN_CSV  = d / "07-3_指标异常结果.csv"
        self.GOUJI_CSV    = d / "07-4_勾稽校验结果.csv"
        self.RISK_CSV     = d / "07-5_汇总风险.csv"

    def mkdir(self):
        self.dir.mkdir(parents=True, exist_ok=True)


# ---------- DeepSeek API ----------
# 密钥只从环境变量读取，禁止硬编码进仓库；
# 未配置时 LLM 环节会自动降级为规则版输出，主流程不中断。
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEEPSEEK_MODEL    = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()

TOLERANCE_GOV_BOND = 0.021
RING_CHANGE_LIMIT  = 0.50
CASH_PROFIT_RATIO  = 0.50
NONREC_RATIO       = 0.30    # 非经常性损益占归母净利润：≥30% 预警
NONREC_HIGH_RATIO  = 0.50    # ≥50% 异常
PARALLEL_PDF     = 4    # step1 同时解析的PDF数（多进程）；设1退回串行
PARALLEL_REPORTS = 4    # step2~6 同时处理的报告数（线程池，受LLM并发限流约束，不宜过大）
VAT_RATE = 0.13            # 销项增值税税率（营收现金含量勾稽用；服务类企业可改0.06）
