import os
import re
from dataclasses import dataclass
from pathlib import Path

BASE_DIR    = Path(__file__).resolve().parent

# 可选：本地 .env 支持（python -m pip install python-dotenv）
# 未安装时静默跳过，直接使用系统环境变量。
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

FILES_DIR   = BASE_DIR / "files"        # 原始财务材料（财报PDF，第一信源）
DRAFTS_DIR  = BASE_DIR / "drafts"       # 研究报告草稿（.md/.txt/.docx/.pdf）
OUTPUT_DIR  = BASE_DIR / "output"
MAPPING_CSV = BASE_DIR / "mapping" / "subject_map.csv"

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')

def make_report_paths(stem: str) -> "ReportPaths":
    name = _INVALID_CHARS.sub("＿", stem.strip()).rstrip(". ")
    return ReportPaths(name or "未命名草稿")

@dataclass
class ReportPaths:
    report_name: str
    def __post_init__(self):
        d = OUTPUT_DIR / self.report_name
        self.dir = d
        # —— 基准侧（沿用原命名习惯）——
        self.RAW_CSV      = d / "01_基线原始提取.csv"
        self.AUDIT_CSV    = d / "01_审计汇总.csv"
        self.ERROR_CSV    = d / "01_错误日志.csv"
        self.KEY_INFO_CSV = d / "01_关键信息.csv"
        self.STD_CSV      = d / "02_标准化数据.csv"
        # —— 草稿核查侧（新增）——
        self.DRAFT_TXT    = d / "03_草稿全文.txt"
        self.CLAIMS_CSV   = d / "03_草稿声明清单.csv"
        self.VERIFY_CSV   = d / "04_核查结果.csv"
        self.FIX_CSV      = d / "05_修改建议.csv"
        self.EVIDENCE_JSON= d / "06_证据链.json"
        self.REPORT_MD    = d / "07_核查报告.md"
        self.PACK_JSON    = d / "08_结构化核查包.json"
        self.SCORE_CSV    = d / "08-1_质量评分.csv"
    def mkdir(self):
        self.dir.mkdir(parents=True, exist_ok=True)

# ---------- DeepSeek API ----------
# 密钥只从环境变量读取，禁止硬编码进仓库；
# 未配置时 LLM 环节会自动降级为规则版输出，主流程不中断。
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEEPSEEK_MODEL    = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()

# ---------- 核查容差（误报控制的单一来源） ----------
NUM_TOL   = 0.005   # 数字核对：差异≤0.5% 视为正确（四舍五入容差）
NUM_SOFT  = 0.02    # 0.5%~2% 之间 → 待复核，不直接判错
MULT_TOL  = 0.02    # 估值倍数重算容差 2%（股价/股本取数日期差异）
UNIT_TOL  = 0.005   # 单位换算识别容差
GOULI_TOL = 0.01    # 草稿内部前后一致容差

# ---------- 严重度分档（差异率 → 等级） ----------
SEVERE_HIGH = 0.50   # >50%  高危
SEVERE_MID  = 0.10   # 10~50% 中

# ---------- 指标纠错支线（依据《研报财务数据比对判定规则.docx》） ----------
# 基准库唯一来源：financial_analysis\output\*\03_指标结果.csv（只读，绝不写入）
FA_OUTPUT_DIR = BASE_DIR / "financial_analysis" / "output"
FA_META_CSV   = BASE_DIR / "financial_analysis" / "mapping" / "company_meta.csv"

# 规则§二：纳入比对的 8 项指标（白名单）
INDICATORS_8 = ["流动比率", "现金流量比率", "资产负债率", "产权比率",
                "毛利率", "毛利率同比pp", "营业现金比率", "资本积累率"]

# 附加指标：不在8项基准内，但研报/数据终端常披露（如Wind衍生指标）。只留痕为"附加观察"
# 提示口径需人工核对（速动比率是否实为保守速动比率、每股经营现金流口径等），不判数值错误。
EXTRA_INDICATORS = ["每股经营现金流", "速动比率", "净资产收益率"]

# 同比pp为差值量纲，相对误差在近零处失效，采用绝对容差（规则§4.3允许组长按精度调整）
PP_TOL = 0.20
