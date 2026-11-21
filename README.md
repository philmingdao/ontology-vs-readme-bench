# `ontology-vs-readme-bench` — 企业级 AI 数据语义层受控对照实验

> **189 行自然语言 `README.md` vs 998 行 OWL 2 RL + SHACL 推理机 vs 裸 RDS Schema**：在 480 次受控 Agentic AI 评测（`deepseek-v4.1-flash`，4 组 × 40 题 × 3 轮，0 报错）中，`README.md` 与全套 OWL + 推理机打成 **100.0% vs 100.0%**，同时 Token 消耗更低、工程维护行数减少 **81%**。

- 深度实验解读博客：[`docs/blog_zh.md`](docs/blog_zh.md)
- 480 次真实评测统计报告：[`results/deepseek_v41_flash_r3/report.md`](results/deepseek_v41_flash_r3/report.md)
- 全部 480 条原始运行轨迹：[`results/deepseek_v41_flash_r3/traces.jsonl`](results/deepseek_v41_flash_r3/traces.jsonl)
- 规则 1-to-1 信息等价审计表：[`docs/information_equivalence.md`](docs/information_equivalence.md)
- 预置企业脏数据测试库：[`data/enterprise.db`](data/enterprise.db)

---

## 一、核心实测结果（`deepseek-v4.1-flash`, `temperature=0.2`, `n=120`/组）

| 对照组 | 提供给 Agent 的语义与工具 | 总通过率 (95% Wilson CI) | 平均总 Token | 平均耗时 | 语义规则 + 推理代码 |
|---|---|---:|---:|---:|---:|
| `bare_rds` | 仅 SQLite DDL + 每表 3 行样例 | **38.3%** (46/120) | 137,204 | 123.5 s | 0 行 |
| `readme_md` | 裸库 + [`semantics/enterprise_readme.md`](semantics/enterprise_readme.md) | **100.0%** (120/120) | **35,470** | 11.2 s | **189 行** |
| `raw_owl` | 裸库 + [`semantics/ontology.ttl`](semantics/ontology.ttl) | **100.0%** (120/120) | 39,508 | 12.3 s | 351 行 |
| `owl_reasoner` | 裸库 + OWL + **`owlrl` 推理机 + `pyshacl` SHACL 引擎** | **100.0%** (120/120) | 38,295 | **11.0 s** | **998 行** (573+425) |

### 四层梯度拆分（每层每组 30 次）

| 难度层级 | `bare_rds` | `readme_md` | `raw_owl` | `owl_reasoner` |
|---|---|---|---|---|
| **Tier 1**：标准表层统计 | **100.0%** (30/30) | **100.0%** (30/30) | **100.0%** (30/30) | **100.0%** (30/30) |
| **Tier 2**：私域口径 / 正数退款 / 扇出陷阱 | **0.0%** (0/30) | **100.0%** (30/30) | **100.0%** (30/30) | **100.0%** (30/30) |
| **Tier 3**：4 层传递闭包 / 类目继承 / 双用途交叉 | **50.0%** (15/30) | **100.0%** (30/30) | **100.0%** (30/30) | **100.0%** (30/30) |
| **Tier 4**：多规则采购单合规拦截与抗诱导 | **3.3%** (1/30) | **100.0%** (30/30) | **100.0%** (30/30) | **100.0%** (30/30) |

- **统计学检验**：`readme_md` 与 `owl_reasoner` 配对 McNemar 精确检验 $p = 1.0000$，TOST ±5% 等效检验 $p < 0.001$（严格统计学等效）。
- **反“稻草人”确认**：`owl_reasoner` 在 Tier 4 的 30 次合规判定中 **30/30 次均实际调用了 `owl_validate_po_shacl`**，且推理机由 950 条三元组物化扩展至 2,374 条；`readme_md` 文档中未夹带任何现成 SQL 或 CTE 模板。

---

## 二、仓库目录结构

```text
ontology-vs-readme-bench/
├── data/
│   └── enterprise.db                # 12 张表的企业脏数据 SQLite 测试库（可由代码确定性重建）
├── docs/
│   ├── blog_zh.md                   # 实验解读博客（含四组同一规则的对照示例）
│   └── information_equivalence.md   # R01–R28 规则在 README / OWL / SHACL 间的 1-to-1 对照表
├── results/
│   └── deepseek_v41_flash_r3/       # 480 次评测完整数据 (traces.jsonl / summary.json / report.md)
├── semantics/
│   ├── enterprise_readme.md         # Arm B：189 行自然语言业务与合规指南
│   ├── ontology.ttl                 # Arm C1/C2：351 行 OWL 2 RL 本体（含 owl:TransitiveProperty / hasValue 等）
│   └── shapes.ttl                   # Arm C2：222 行 SHACL-SPARQL 合规校验规则
├── src/bench/
│   ├── db_builder.py                # 确定性企业脏库生成器
│   ├── owl_engine.py                # 基于 rdflib + owlrl + pyshacl 的真实推理与校验引擎
│   ├── tasks.py                     # 40 道分层题集（T1_01–T4_10）及 SQL / Python 真值计算
│   ├── evaluator.py                 # 数值容差、表格行集、决策及违规规则集 F1 评分器
│   ├── tools.py                     # 只读 SQLite 工具 + 4 个 OWL/SHACL 推理工具
│   ├── agent.py                     # OpenAI 兼容多轮工具调用执行器与离线 Mock 模拟器
│   ├── stats_report.py              # Wilson CI / exact McNemar / paired TOST / ROI 报告生成器
│   └── cli.py                       # ovr-bench 命令行入口
└── tests/
    └── test_integrity.py            # 数据库、OWL 闭包、SHACL 等价性及 CLI 冒烟测试
```

---

## 三、快速复现实验

### 1. 克隆并安装环境

```bash
git clone https://github.com/philmingdao/ontology-vs-readme-bench.git
cd ontology-vs-readme-bench

uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e .
```

### 2. 校验真值一致性与单元测试

```bash
# 重新生成确定性 SQLite 测试库
ovr-bench build-db

# 验证全部 40 题 SQL 真值与 OWL 2 RL + SHACL 推理机结果 100% 一致
ovr-bench verify

# 运行完整单元测试
pytest -v
```

### 3. 离线跑通评测流水线（零 API 消耗）

```bash
ovr-bench run --mock --output-dir results/mock_smoke
```

### 4. 使用自己的 OpenAI 兼容模型开跑（OpenAI / DeepSeek / OpenRouter / Ollama）

```bash
cp .env.example .env
# 在 .env 中填入 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL

ovr-bench run \
  --model gpt-4o-mini \
  --runs 3 \
  --temperature 0.2 \
  --concurrency 8 \
  --resume \
  --output-dir results/my_model_r3
```

汇总多模型横向对比报告：

```bash
ovr-bench report \
  results/deepseek_v41_flash_r3/traces.jsonl \
  results/my_model_r3/traces.jsonl \
  --output-dir results/combined_report
```
