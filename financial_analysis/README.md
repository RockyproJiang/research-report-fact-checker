# 上市公司年报智能分析流水线（阶段一 · 基准库）

> 本文档是**阶段一（`financial_analysis/`）**的说明：把上市公司年报 PDF 变成可复用的财务基准库。
> 整个项目的总览、阶段二「研报纠错」的用法与判定规则，请见根目录 [README.md](../README.md)。

## 项目简介

本子项目面向 A 股上市公司年度报告（PDF）的自动化财务分析。  
输入为上市公司年报 PDF，输出包括：

- 标准化后的三张合并财务报表数据
- 财务指标计算结果（含阶段二所需的 8 项基准比率）
- 勾稽校验结果
- 异常清单与行业特性豁免记录
- 基于大模型（DeepSeek，可选）生成的 Markdown 分析报告
- 结构化基准包（JSON + CSV），便于二次开发与批量对比

项目采用“两阶段批处理”架构：  
**阶段一**：并行解析 PDF，提取报表与关键信息；  
**阶段二**：完成标准化、指标计算、异常检测、LLM 报告生成与结构化输出。

阶段一的产物 `output/<公司：报告期>/03_指标结果.csv` 是阶段二**唯一**的比对基准来源（只读，绝不写入）。

---

## 功能特性

- **PDF 报表提取**：自动识别合并资产负债表、合并利润表、合并现金流量表，保留 PDF 页码与表格位置。
- **关键信息提取**：审计意见、归母净利润、扣非归母净利润、非经常性损益合计、现金分红、总股本、合并范围变化等。
- **科目映射标准化**：通过 `mapping/subject_map.csv` 将不同公司报表科目统一为标准科目。
- **财务指标计算**：流动比率、现金流量比率、资产负债率、产权比率、毛利率、毛利率同比pp、营业现金比率、资本积累率、ROE、净利率、周转率、净现比等。
- **勾稽校验**：资产负债表平衡、负债分项之和、营业利润勾稽、净利润勾稽、现金净增加勾稽、未分配利润勾稽、营收现金含量、成本现金支付、连续期勾稽。
- **异常规则引擎**：阈值告警、审计意见非标、退市风险、持续经营、利润现金流背离、非经常性损益占比、会计口径变化、环比骤变、纸面利润等。
- **行业特性豁免**：通过 `mapping/industry_profile.csv` 对特定行业的常见异常进行降级处理，避免误报。
- **LLM 归因分析**：调用 DeepSeek 模型生成专业财务分析报告（含事实、逻辑链、推测、核查路径）；未配置 API Key 时自动回退规则版报告。
- **结构化输出**：生成 `07_结构化基准包.json` 及多个 CSV，便于批量汇总与下游系统消费。
- **源文件一致性修复**：自动检测并修正如“负债合计≠流动负债+非流动负债”等内部不一致问题，并在报告中留痕。

---

## 目录结构

```
financial_analysis/
├── main.py                     # 总入口，两阶段批处理
├── config.py                   # 全局配置（路径、API（读环境变量）、阈值、并行度）
├── company_meta.py             # 公司元数据解析（股票代码 + 所属行业）
├── extract_financial_datav2.py # PDF 三张报表提取核心
├── extract_key_info.py         # 关键辅助信息提取
├── step1_extract.py            # Step1：并行解析 PDF
├── step2_standardize.py        # Step2：科目映射与标准化
├── step3_indicators.py         # Step3：指标计算与勾稽校验
├── step4_rules.py              # Step4：规则引擎与异常检测
├── step5_llm_report.py         # Step5：LLM 分析报告生成
├── step6_structured.py         # Step6：结构化基准包与行业豁免
├── debug_map.py                # 映射覆盖体检工具
├── files/                      # 存放待分析的年报 PDF（*.pdf 不入库，目录已用 .gitkeep 占位）
├── mapping/
│   ├── subject_map.csv         # 科目映射表
│   ├── company_meta.csv        # 公司元数据（股票代码、行业）
│   ├── industry_keywords.csv   # 行业关键词推断表
│   └── industry_profile.csv    # 行业特性豁免规则
├── output/                     # 运行后自动生成的分析结果（仓库内带 10 份示例）
└── README.md
```

---

## 环境依赖

- **Python 3.10+**（代码使用 `X | None` 类型注解；开发与验证环境为 3.13）
- 主要依赖：
  - `pdfplumber` —— PDF 文本层解析
  - `pandas` —— 数据处理
  - `openai` —— 调用 DeepSeek（兼容 OpenAI 协议）
- 安装（推荐用项目根的统一清单，已覆盖本子项目全部依赖）：

```bash
# 在项目根目录
python -m pip install -r requirements.txt
```

也可只装本子项目依赖：

```bash
cd financial_analysis
python -m pip install -r requirements.txt
```

> `pdfplumber` 读取的是 PDF 的**文本层**；扫描版（纯图片）年报需要先 OCR，否则提取结果为空。

---

## 配置说明

### 1. DeepSeek API

**密钥不写在代码里。** `config.py` 只从环境变量读取，仓库中不含任何 Key：

```powershell
# PowerShell
$env:DEEPSEEK_API_KEY = "sk-..."
```

```bash
# bash / zsh
export DEEPSEEK_API_KEY="sk-..."
```

| 环境变量 | 是否必填 | 默认值 | 说明 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | LLM 报告需要 | 空 | 未设置时跳过 LLM，只输出规则版结果 |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com` | 兼容 OpenAI 协议的任意端点 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-v4-flash` | 模型名 |

也可以把变量写进项目根或本目录的 `.env`（已在 `.gitignore` 中屏蔽，安装 `python-dotenv` 后自动加载）。

### 2. 并行度

```python
PARALLEL_PDF     = 4    # Step1 同时解析的 PDF 数（多进程）；设 1 退回串行
PARALLEL_REPORTS = 4    # Step2~6 同时处理的报告数（多线程）
```

根据机器性能与 API 限流情况调整。

### 3. 阈值与税率

```python
TOLERANCE_GOV_BOND = 0.021   # 国债收益率容差
RING_CHANGE_LIMIT  = 0.50    # 环比骤变阈值
CASH_PROFIT_RATIO  = 0.50    # 净现比预警阈值
NONREC_RATIO       = 0.30    # 非经常性损益占归母净利润预警
NONREC_HIGH_RATIO  = 0.50    # 非经常性损益占归母净利润异常
VAT_RATE           = 0.13    # 销项增值税税率
```

### 4. 行业映射

- `mapping/company_meta.csv`：公司名称 → 股票代码、所属行业。
- `mapping/industry_keywords.csv`：关键词 → 所属行业，用于自动推断。
- `mapping/industry_profile.csv`：行业特性豁免规则，例如半导体行业高流动比率、重资产行业成本现金支付差异等。

---

## 使用方法

### 1. 准备 PDF

将年报 PDF 放入 `files/` 目录，文件名建议包含公司名称与报告期，例如演示：

```
files/中芯国际：2025年年度报告.pdf
files/兆易创新：2025年年度报告.pdf
files/仕净科技：2025年年度报告.pdf
files/中芯国际：2024年年度报告.pdf
files/兆易创新：2024年年度报告.pdf
files/仕净科技：2024年年度报告.pdf
```

> 出于体积与版权考虑，仓库不包含任何 PDF，`*.pdf` 已在 `.gitignore` 中屏蔽；目录本身用 `.gitkeep` 占位，克隆后直接放入文件即可。

### 2. 运行主流程

```bash
python main.py
```

运行过程会：

1. 清空 `output/`（可通过环境变量 `KEEP_OUTPUT=1` 跳过清空）；
2. 并行解析 PDF，生成 `01_原始提取.csv`、`01_关键信息.csv` 等；
3. 标准化所有报告，全局组装；
4. 逐份计算指标、勾稽、异常，应用行业豁免；
5. 调用 LLM 生成 Markdown 分析报告（无 Key 时回退规则版）；
6. 生成结构化基准包与汇总风险。

> ⚠️ 若要保留 `output/` 下已有的基准库，务必先设 `KEEP_OUTPUT=1`，否则会被一并删除：
>
> ```powershell
> $env:KEEP_OUTPUT = "1"
> python main.py
> ```

### 3. 查看结果

运行完成后，在 `output/` 下按报告名称生成子目录，每个子目录包含：

| 文件 | 说明 |
|---|---|
| `01_原始提取.csv` | 三张报表原始提取数据，含 PDF 位置 |
| `01_审计汇总.csv` | 提取状态汇总 |
| `01_错误日志.csv` | 解析错误记录 |
| `01_关键信息.csv` | 审计意见、净利润、分红等关键字段 |
| `02_标准化数据.csv` | 科目映射后的标准化数据 |
| `03_指标结果.csv` | 全部财务指标（**阶段二的比对基准库**） |
| `04_异常清单.csv` | 异常项、等级、权重、行业豁免标记 |
| `05_证据链.json` | 科目证据位置、指标、关键信息、跨报告衔接 |
| `06_分析报告.md` | LLM 生成的 Markdown 分析报告 |
| `07_结构化基准包.json` | 完整结构化输出 |
| `07-1_基础信息.csv` | 基础信息 |
| `07-2_指标计算结果.csv` | 指标计算结果 |
| `07-3_指标异常结果.csv` | 指标异常结果 |
| `07-4_勾稽校验结果.csv` | 勾稽校验结果 |
| `07-5_汇总风险.csv` | 整体风险等级与描述 |

此外，`output/00_批处理汇总.csv` 汇总所有报告的风险等级，`output/run.log` 记录完整运行日志（已 gitignore）。

---

## 映射覆盖体检

若发现某些科目未映射，可运行：

```bash
python debug_map.py "中芯国际：2025年年度报告"
```

输出：

- 未映射科目列表
- 关键标准化科目覆盖检查
- 利润表“本期有值、上期缺失”的科目

根据提示补充 `mapping/subject_map.csv` 后重跑。

---

## 自定义与扩展

### 新增公司行业

编辑 `mapping/company_meta.csv`，添加：

```csv
公司名称,股票代码,所属行业
贵州茅台,600519.SH,白酒
```

### 新增行业关键词

编辑 `mapping/industry_keywords.csv`：

```csv
关键词,所属行业
宁德时代,锂电池
```

### 新增行业豁免规则

编辑 `mapping/industry_profile.csv`：

```csv
行业关键词,指标,方向,豁免等级,豁免理由
锂电池,流动比率,过高,预警,行业现金流充沛，高流动比率为常态
```

### 调整异常阈值

修改 `step3_indicators.py` 中的 `THRESHOLDS` 列表，或调整 `config.py` 中的全局参数。

---

## 注意事项

1. **PDF 质量**：扫描版或排版异常的 PDF 可能导致提取失败，建议使用文本层清晰的年报。
2. **API 费用**：LLM 归因分析会调用 DeepSeek API，产生费用，请留意额度；未配置 `DEEPSEEK_API_KEY` 时该步骤自动跳过，不产生费用也不影响其它输出。
3. **并发限制**：`PARALLEL_REPORTS` 过大可能触发 API 限流，建议 2~4。
4. **数据一致性**：若源文件存在内部不一致（如负债合计与分项不符），系统会修正并在报告中标注“源文件内部不一致，相关金额暂不可定稿”。
5. **行业豁免**：豁免项权重置 0，不计入风险得分，但仍保留在异常清单中供追溯。
6. **风险等级判定**：
   - 存在未豁免的“极高/高风险”项，或加权得分 > 60 → 极高风险
   - 得分 > 30 → 高风险
   - 得分 > 10 → 中风险
   - 否则 → 低风险

---

## 许可证

[MIT](../LICENSE) © 研究报告纠错 project contributors

---

## 免责声明

本工具仅用于辅助财务分析，输出结果不构成投资建议。  
LLM 生成内容可能存在偏差，请务必结合原始年报与专业判断进行复核。
