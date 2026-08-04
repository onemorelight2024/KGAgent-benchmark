# ChronoQG

[中文说明](README.zh-CN.md)

ChronoQG is a benchmark dataset and construction pipeline for Temporal Knowledge Graph Question Generation (TKGQG), corresponding to the paper "ChronoQG: Towards a Temporally Expressive and Hop-Bounded Benchmark for Temporal Knowledge Graph Question Generation". It first samples topology-temporal query traces from knowledge graphs according to temporal relations, then constructs benchmark records from those traces, and finally uses LLM-based rewriting and verification to obtain natural-language questions with traceable answers. KGAgent vendors the core construction code as a benchmark method adapter; released ChronoQG dataset JSONL files are intentionally not bundled in the source package.

## Dataset

The upstream ChronoQG release contains four JSONL datasets:

| File | Split | Source KG | Rows |
|---|---|---|---:|
| `data/cronkg-s.jsonl` | Cron-S | CronKG | 5,213 |
| `data/cronkg-m.jsonl` | Cron-M | CronKG | 1,599 |
| `data/eventkg-s.jsonl` | Event-S | EventKG | 6,191 |
| `data/eventkg-m.jsonl` | Event-M | EventKG | 2,183 |
| Total |  |  | 15,186 |

`-S` denotes single-temporal-constraint questions, and `-M` denotes multi-temporal-constraint questions.

These released data files are not committed under KGAgent's `src/` tree because they are large static datasets and are not required by the KGAgent runtime adapter. Store them in an external dataset location or a dedicated data PR if you need to reproduce the original ChronoQG release directly.

Each JSONL record uses the unified release schema:

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

Field summary:

- `subgraph`: temporal KG facts used by the instance, including entity/relation ids, text labels, timestamps, and fact ids.
- `space_search_process`: candidate-space search and filtering trace.
- `temporal_constraints`: temporal constraints used during construction; the retained binding information can be used to trace which facts, relations, or candidate histories each constraint attaches to.
- `raw_question`: template-style question before LLM rewriting.
- `final_question`: verified natural-language question.
- `metadata`: provenance and construction records, such as template id, source sample id, answer id, routing result, and verification status.

The field summary above describes the released JSONL schema used by the original dataset files.

## Quick Start

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

To reuse the framework and rebuild from a local temporal knowledge graph, prepare a KG directory with the following files:

```text
kg_dir/
├── full.txt                    # subject<TAB>relation<TAB>object<TAB>start<TAB>end
├── wd_id2entity_text.txt        # entity_id<TAB>entity text
└── wd_id2relation_text.txt      # relation_id<TAB>relation text
```

`start` and `end` are integers at the configured temporal granularity:

- `year`: `YYYY`, for example `1999`
- `month`: `YYYYMM`, for example `199905`
- `day`: `YYYYMMDD`, for example `19990523`

Point facts normally use `start == end`. Interval facts use `start <= end`. Rows with `start > end` are ignored during sampling.

Generate a config file:

```bash
python -m chrono_qg.main init-config \
  --kg-dir /path/to/kg_dir \
  --granularity year \
  -o config.json
```

Before running, check and edit the key fields in `config.json`:

- `kg_path`, `entity_map_path`, `relation_map_path`: input KG files.
- `relation_type_tsv`: optional relation temporal-type file, formatted as `relation_id<TAB>P|I|D`.
- `output_dir`: output directory for traces, intermediate benchmarks, and verified data.
- `time_granularity`: `year`, `month`, or `day`.
- `per_code`: maximum number of candidate examples selected per temporal constraint type before verification.
- `rewrite_model`, `answer_model`, `judge_model`: LLMs used for rewriting, answering, and judging.
- `api_base_url`, `api_key`: legacy fields kept for config compatibility; LLM calls use Claude SDK routing.
- `parallelism`: number of parallel workers in the verification stage.

The LLM verification stage uses Claude Agent SDK. Recommended settings are the
same as KGAgent chat: activate CCR before running.

```bash
ccr restart
eval "$(ccr activate)"
export ANTHROPIC_MODEL="gpt-5.4"
```

Run the full released pipeline:

```bash
python -m chrono_qg.main run --config config.json
```

Or run stages separately:

```bash
python -m chrono_qg.main sample --config config.json
python -m chrono_qg.main build --config config.json
python -m chrono_qg.main verify --config config.json --tc-mode tc1
python -m chrono_qg.main verify --config config.json --tc-mode tc_gt1
```

Default output structure:

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

The verified outputs use the same schema as the released ChronoQG data.

## Code Architecture

The released pipeline corresponds to the three-stage framework in the paper.

1. `sample`: sample topology-temporal query traces from a temporal knowledge graph.
2. `build`: convert trace samples into benchmark records.
3. `verify`: rewrite template questions into natural language and verify that the answer is preserved.

Core files:

- `chrono_qg/main.py`: command-line entry point. It wires together `sample -> build -> verify`, reads the config, and injects global settings such as time granularity and relation types.
- `chrono_qg/tkgqg_config.py`: configuration definitions, including KG paths, output paths, time granularity, sampling parameters, LLM settings, and Allen temporal relation descriptions.
- `chrono_qg/build_temporal_query_traces.py`: trace sampling script. It constructs the seed candidate set, applies hop-bounded templates, and performs backward/forward traversal and temporal filtering.
- `chrono_qg/build_tkgqg_benchmark.py`: benchmark construction script. It selects single-constraint and multi-constraint examples by temporal constraint code and generates trace-grounded benchmark records.
- `chrono_qg/eval_benchmark.py`: rewriting and verification script. It performs question rewriting, answer-model prediction, equivalence judging, strict verification/repair, and writes `dataset.jsonl`, `dataset_pp.jsonl`, and `discarded.jsonl`.
- `chrono_qg/verify_tkgqg_gold_benchmark.py`: helper functions reused by the verification stage.
- `chrono_qg/llm_client.py`: Claude SDK LLM client with lightweight usage accounting.
- `chrono_qg/prompts.py`: prompt templates for rewriting, answering, judging, repair, and strict verification.
- `chrono_qg/allen.py`: utilities for Allen temporal relations.
- `chrono_qg/tkgqg_shared.py`: shared constants and helper functions, including trace rendering, temporal constraints, benchmark records, and JSONL I/O.

## License

MIT License. See `LICENSE`.
