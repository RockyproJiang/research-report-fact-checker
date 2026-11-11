# 研报财务数字事实核查工具

券商研报财务数字事实核查工具——用 pdfplumber 解析年报建可复用指标基准库，抽取研报中带页码与原文的财务声明，按（公司，报告期）关联并归一化（百分数≡小数、单位同量纲）后以 0.5% 容差比对，输出《疑似错误清单》。

## 功能特性

- **问题背景**：研报常出现抄错年份、单位混用（亿元/万元）、口径串用（归母 vs 合并）、四舍五入失真、引用错年份公告；人工逐页回查年报效率低且易漏。
- **两条流水线**：`financial_analysis/`（年报分析器副本）负责基准库构建；根目录脚本负责研报声明抽取与核查，两者通过 `03_指标结果.csv` 衔接。
- **8 项基准指标**：标准化三张报表后计算流动比率、资产负债率、毛利率、营业现金比率、资本积累率等 8 项比率，并做勾稽校验与行业特性豁免。
- **声明抽取带页码与原文**：逐页解析研报 PDF，用 `2024A / 2025E` 等列头给数值绑定报告期，每条声明保留页码、行号、原文与单位。
- **关联与归一化**：按 `(公司, 报告期)` 关联基准库；`40.22% ≡ 0.4022`、亿元/万元/元同量纲换算后再比对。
- **0.5% 容差判定**：差异 ≤ 0.5% 视为正确（可由披露精度解释的舍入差异剔除），0.5%~2% 记「待复核」，超差判为数值不一致或年份不一致。
- **防误报控制**：预测值、单季度值、TTM、估算值与 8 项清单之外的指标只留痕不比对；口径差异单独标记。
- **LLM 复核与修改建议**：`step4_llm_fix.py` 生成可直接替换原文的修改文本；无 Key、断网或额度耗尽时自动回退规则版建议，主流程不中断。
- **结构化输出**：核查结果、修改建议、证据链、核查报告与结构化核查包（CSV / JSON / MD）。

## 技术栈 / 依赖

- Python >= 3.10（代码使用 `X | None` 类型注解）
- `pandas>=2.0`、`pdfplumber>=0.10`、`openai>=1.30`、`python-docx>=1.1`（见 `requirements.txt`）
- 可选：`python-dotenv>=1.0`（自动加载根目录 `.env`，不装则直接用系统环境变量）
- 可选：DeepSeek API（LLM 复核环节，未配置时降级为规则版）

## 目录结构

```text
research-report-fact-checker/
├── main.py                    # 根目录总入口：全口径研报核查流水线
├── main_indicators.py         # 8 项基准比率专项核查入口
├── config.py                 # 路径、API、容差（NUM_TOL=0.005）与严重度分档
├── draft_parser.py           # 研报 PDF/文本解析，抽取带页码的财务声明
├── ind_baseline.py           # 指标基准库关联
├── ind_claims.py             # 8 项指标声明抽取
├── step1_baseline.py         # 基准库准备
├── step2_standardize.py      # 研报声明标准化
├── step3_verify.py           # 核查主流程
├── step3a_scope.py           # 范围判定（预测/单季/TTM 跳过）
├── step3b_compare.py         # 归一化比对与容差判定
├── step4_llm_fix.py          # LLM 复核与修改建议生成
├── step5_structured.py       # 结构化核查包输出
├── debug_draft.py            # 研报解析调试
├── regen_moutai_baseline.py  # 贵州茅台基准库重建脚本
├── test_e2e_inject.py        # 端到端注入测试
├── extract_financial_datav2.py / extract_key_info.py  # 财报提取工具
├── 实现步骤.md               # 实施步骤说明
├── 研报财务数据比对判定规则.docx  # 比对判定规则文档
├── requirements.txt
├── .gitignore
├── LICENSE                   # MIT
├── README.md
├── files/                    # 研报 PDF 样本
├── mapping/subject_map.csv   # 科目映射
├── output/                   # 核查结果（按报告分目录）
└── financial_analysis/       # 年报分析器完整副本（基准库流水线）
    ├── main.py / config.py / step1_extract.py ~ step6_structured.py
    ├── files/  mapping/  output/
    └── README.md
```

## 快速开始

1. 克隆与安装依赖：

```bash
git clone https://github.com/RockyproJiang/research-report-fact-checker.git
cd research-report-fact-checker
pip install -r requirements.txt
```

2. 将研报 PDF 放入 `files/`，年报 PDF 放入 `financial_analysis/files/`（用于构建基准库）。

3. 运行核查流水线：

```bash
python main.py
```

`main.py` 支持 `python main.py --init` 清空 `output/` 后重跑；另可运行 `python main_indicators.py` 做 8 项基准比率专项核查。基准库来源为 `financial_analysis/output/*/03_指标结果.csv`（只读，不写入）。

## 配置与安全说明

- **API Key 处理方式**：根目录 `config.py` 第 54 行与 `financial_analysis/config.py` 第 59 行均为 `DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()`，即**空默认值、不硬编码任何密钥**；调用大模型复核需设置环境变量 `DEEPSEEK_API_KEY`，未配置时 LLM 环节自动降级为规则版输出，主流程不中断。根目录 `config.py` 在安装了 `python-dotenv` 时会尝试加载项目根目录 `.env`，未安装则静默跳过。
- **仓库中不含任何真实 API Key**。请勿把密钥写进代码或提交到 Git；配置时只使用明显占位值，例如在项目根目录 `.env` 中写：

```bash
DEEPSEEK_API_KEY=sk-xxx
```

  或在 shell 中 `export DEEPSEEK_API_KEY=你的密钥`。请确认 `.gitignore` 已忽略 `.env`（当前已包含 `.env` 与 `.env.*`）。`DEEPSEEK_BASE_URL`（默认 `https://api.deepseek.com`）与 `DEEPSEEK_MODEL` 同样可通过环境变量覆盖。
- `files/` 的研报 PDF、`financial_analysis/files/` 的年报 PDF 与 `output/` 结果仅作样本与演示，注意版权与敏感资料，请勿随仓库公开敏感内部资料。
- 已清理误提交的 `__pycache__/` 并补充 `.gitignore`（忽略 `__pycache__/`、`*.py[cod]`、`.env` 等），避免缓存与密钥入库。
- 本工具输出仅供研究与辅助核查，不构成投资建议；判定结论须人工复核。

## 许可证

本项目采用 MIT License，详见 `LICENSE`（Copyright (c) 2026 研究报告纠错 project contributors）。
