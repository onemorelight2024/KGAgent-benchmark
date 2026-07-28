# ChronoQG

ChronoQG 是一个面向时间知识图谱问题生成 (TKGQG) 的基准数据集和构建流水线，对应论文 “ChronoQG: Towards a Temporally Expressive and Hop-Bounded Benchmark for Temporal Knowledge Graph Question Generation” 。它先根据时序关系从知识图谱中采样拓扑-时间查询轨迹，再基于轨迹构造 benchmark record，最后通过 LLM 改写和验证得到自然语言问题及其可追溯答案。KGAgent 将核心构建代码作为 benchmark method adapter 引入；发布版 ChronoQG 数据集 JSONL 不随源码包内置。

## 数据集

上游 ChronoQG 发布版包含四个 JSONL 数据集：

| 文件 | 划分 | 来源 KG | 样本数 |
|---|---|---|---:|
| `data/cronkg-s.jsonl` | Cron-S | CronKG | 5,213 |
| `data/cronkg-m.jsonl` | Cron-M | CronKG | 1,599 |
| `data/eventkg-s.jsonl` | Event-S | EventKG | 6,191 |
| `data/eventkg-m.jsonl` | Event-M | EventKG | 2,183 |
| 总计 |  |  | 15,186 |

`-S` 表示单时间约束问题，`-M` 表示多时间约束问题。

这些发布数据文件不提交到 KGAgent 的 `src/` 目录，因为它们属于体积较大的静态数据，当前 KGAgent runtime adapter 不依赖它们。如果需要直接复现原始 ChronoQG 发布数据，应放在外部数据目录或单独的数据 PR 中管理。

每条 JSONL 记录使用统一发布字段：

```json
{
  "id": "...",
  "graph_structure": "...",
  "hop_count": 2,
  "subgraph": [...],
  "space_search_process": [...],
  "temporal_constraints": [...],
  "temporal_constraint_count": 1,
  "raw_question": "...",
  "final_question": "...",
  "answer": "...",
  "metadata": {...}
}
```

字段概要：

- `subgraph`：该样本使用的时间 KG 事实，包含实体/关系 id、文本、时间戳和 fact id。
- `space_search_process`：候选空间搜索与过滤轨迹。
- `temporal_constraints`：构造过程中使用的时间约束；其中保留 binding 信息，用于追踪约束作用在哪些事实、关系或候选历史上。
- `raw_question`：LLM 改写前的模板式问题。
- `final_question`：验证后的自然语言问题。
- `metadata`：来源和过程记录，例如模板 id、源样本 id、答案 id、路由结果和验证状态。

上述字段说明适用于原始发布版 JSONL 数据文件。

## Quick Start

安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如果需要复用框架从本地时间知识图谱重新构建，需要准备如下 KG 目录：

```text
kg_dir/
├── full.txt                    # subject<TAB>relation<TAB>object<TAB>start<TAB>end
├── wd_id2entity_text.txt        # entity_id<TAB>实体文本
└── wd_id2relation_text.txt      # relation_id<TAB>关系文本
```

`start` 和 `end` 是与配置粒度一致的整数：

- `year`：`YYYY`，例如 `1999`
- `month`：`YYYYMM`，例如 `199905`
- `day`：`YYYYMMDD`，例如 `19990523`

点事实通常使用 `start == end`。区间事实使用 `start <= end`。如果一行满足 `start > end`，采样时会被忽略。

生成配置文件：

```bash
python -m chrono_qg.main init-config \
  --kg-dir /path/to/kg_dir \
  --granularity year \
  -o config.json
```

运行前检查并修改 `config.json` 中的关键字段：

- `kg_path`、`entity_map_path`、`relation_map_path`：输入 KG 文件。
- `relation_type_tsv`：可选的关系时间类型文件，格式为 `relation_id<TAB>P|I|D`。
- `output_dir`：轨迹、中间 benchmark 和验证后数据的输出目录。
- `time_granularity`：`year`、`month` 或 `day`。
- `per_code`：验证前每类时间约束最多选择的候选样本数。
- `rewrite_model`、`answer_model`、`judge_model`：用于改写、回答和判断的 LLM。
- `api_base_url`、`api_key`：可选的配置内 API 设置。
- `parallelism`：验证阶段并发数。

LLM 验证阶段使用 OpenAI-compatible API。推荐设置：

```bash
export OPENAI_API_KEY="..."
export LLM_BASE_URL="https://api.openai.com/v1"
```

运行完整发布版流水线：

```bash
python -m chrono_qg.main run --config config.json
```

也可以分阶段运行：

```bash
python -m chrono_qg.main sample --config config.json
python -m chrono_qg.main build --config config.json
python -m chrono_qg.main verify --config config.json --tc-mode tc1
python -m chrono_qg.main verify --config config.json --tc-mode tc_gt1
```

默认输出结构：

```text
output/
├── traces/trace_samples.jsonl
├── benchmark/benchmark_tc1.jsonl
├── benchmark/benchmark_tc_gt1.jsonl
└── verified/
    ├── tc1/dataset.jsonl
    ├── tc1/dataset_pp.jsonl
    ├── tc_gt1/dataset.jsonl
    └── tc_gt1/dataset_pp.jsonl
```

验证后的输出字段与 ChronoQG 发布数据的字段一致。

## 代码架构

发布版流水线对应论文中的三阶段框架。

1. `sample`：从时间知识图谱采样拓扑-时间查询轨迹。
2. `build`：将 trace samples 转换成 benchmark records。
3. `verify`：将模板问题改写为自然语言，并验证答案保持一致。

核心文件：

- `chrono_qg/main.py`：命令行入口，串联 `sample -> build -> verify`，读取配置并注入时间粒度、关系类型等全局设置。
- `chrono_qg/tkgqg_config.py`：配置定义，包括 KG 路径、输出路径、时间粒度、采样参数、LLM 设置和 Allen 时间关系描述。
- `chrono_qg/build_temporal_query_traces.py`：轨迹采样脚本，负责构造 seed candidate set、应用 hop-bounded template、执行 backward/forward traversal 和 temporal filter。
- `chrono_qg/build_tkgqg_benchmark.py`：benchmark 构建脚本，按时间约束 code 选择单约束/多约束样本，生成 trace-grounded benchmark records。
- `chrono_qg/eval_benchmark.py`：改写与验证脚本，执行问题改写、答案模型回答、等价性判断、严格验证/修复，并输出 `dataset.jsonl`、`dataset_pp.jsonl` 和 `discarded.jsonl`。
- `chrono_qg/verify_tkgqg_gold_benchmark.py`：验证阶段复用的辅助函数。
- `chrono_qg/llm_client.py`：OpenAI-compatible LLM 客户端，包含重试和 token usage 统计。
- `chrono_qg/prompts.py`：改写、回答、判断、修复和严格验证使用的 prompt 模板。
- `chrono_qg/allen.py`：Allen 时间关系相关工具。
- `chrono_qg/tkgqg_shared.py`：共享常量和工具函数，包括 trace 渲染、时间约束、benchmark record 和 JSONL I/O。

## 许可证

MIT License。见 `LICENSE`。
