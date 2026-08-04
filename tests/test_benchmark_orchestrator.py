import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

from kgagent.benchmark.tools.language import detect_graph_language
from kgagent.benchmark.orchestrator import run_benchmark


def _fake_method_output(batch, method):
    rows = []
    for item in batch:
        answer = item["answer"]
        if answer.get("type") == "time":
            question = "When did the supported temporal fact hold?"
        else:
            question = f"What is related to {answer['text']}?"
        rows.append(
            {
                "sample_id": item["sample_id"],
                "generated_question": question,
                "answer": answer,
                "subgraph": item.get("subgraph") or item.get("temporal_subgraph", {}),
                "constraints": item.get("constraints", {}),
                "source": item.get("source", {}),
                "graph_type": item.get("graph_type", "KG"),
                "method": method,
                "metadata": {},
            }
        )
    return rows


def test_run_benchmark_batches_and_resumes(tmp_path, monkeypatch):
    calls = {"count": 0}

    def flaky_batch(**kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("simulated interruption")
        rows = _fake_method_output(kwargs["batch"], kwargs["method"])
        return {"items": rows, "output_path": str(kwargs["output_path"]), "method": kwargs["method"]}

    monkeypatch.setattr("kgagent.benchmark.orchestrator._run_one_method_batch", flaky_batch)

    with pytest.raises(RuntimeError):
        asyncio.run(
            run_benchmark(
                data="examples/kg_benchmark_input.json",
                graph_type="KG",
                task="KGQA",
                method="sgsh_prompt",
                sample_count=5,
                model="gpt-5.4",
                work_dir=Path.cwd(),
                output_dir=tmp_path,
                run_id="resume_case",
                batch_size=2,
                resume=True,
            )
        )

    run_dir = tmp_path / "benchmark_runs" / "resume_case"
    state = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
    assert state["progress"]["method_done"] == 2
    assert sum(1 for _ in (run_dir / "method_output.jsonl").open(encoding="utf-8")) == 2

    def normal_batch(**kwargs):
        rows = _fake_method_output(kwargs["batch"], kwargs["method"])
        return {"items": rows, "output_path": str(kwargs["output_path"]), "method": kwargs["method"]}

    monkeypatch.setattr("kgagent.benchmark.orchestrator._run_one_method_batch", normal_batch)

    result = asyncio.run(
        run_benchmark(
            data="examples/kg_benchmark_input.json",
            graph_type="KG",
            task="KGQA",
            method="sgsh_prompt",
            sample_count=5,
            model="gpt-5.4",
            work_dir=Path.cwd(),
            output_dir=tmp_path,
            run_id="resume_case",
            batch_size=2,
            resume=True,
        )
    )

    assert result["batch_size"] == 2
    assert result["stats"]["total"] == 5
    assert result["run_state_path"].endswith("run_state.json")
    final_state = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
    assert final_state["status"] == "done"
    assert final_state["progress"]["method_done"] == 5


def test_default_run_id_is_stable_for_same_graph_path(tmp_path, monkeypatch):
    calls = {"count": 0}

    def normal_batch(**kwargs):
        calls["count"] += 1
        rows = _fake_method_output(kwargs["batch"], kwargs["method"])
        return {"items": rows, "output_path": str(kwargs["output_path"]), "method": kwargs["method"]}

    monkeypatch.setattr("kgagent.benchmark.orchestrator._run_one_method_batch", normal_batch)

    first = asyncio.run(
        run_benchmark(
            data="examples/kg_benchmark_input.json",
            graph_type="KG",
            task="KGQA",
            method="sgsh_prompt",
            sample_count=2,
            model="gpt-5.4",
            work_dir=Path.cwd(),
            output_dir=tmp_path,
            resume=True,
        )
    )
    second = asyncio.run(
        run_benchmark(
            data="examples/kg_benchmark_input.json",
            graph_type="KG",
            task="KGQA",
            method="sgsh_prompt",
            sample_count=2,
            model="gpt-5.4",
            work_dir=Path.cwd(),
            output_dir=tmp_path,
            resume=True,
        )
    )

    assert first["run_id"] == second["run_id"]
    assert first["run_id"].startswith("kgqa_kg_sgsh_prompt_kg_benchmark_input_")
    assert calls["count"] == 1
    assert second["stats"]["total"] == 2


def test_run_benchmark_passes_language_to_method_input(tmp_path, monkeypatch):
    seen_languages = []

    def normal_batch(**kwargs):
        seen_languages.extend(item.get("language") for item in kwargs["batch"])
        rows = _fake_method_output(kwargs["batch"], kwargs["method"])
        return {"items": rows, "output_path": str(kwargs["output_path"]), "method": kwargs["method"]}

    monkeypatch.setattr("kgagent.benchmark.orchestrator._run_one_method_batch", normal_batch)

    result = asyncio.run(
        run_benchmark(
            data="examples/kg_benchmark_input.json",
            graph_type="KG",
            task="KGQA",
            method="sgsh_prompt",
            sample_count=2,
            model="gpt-5.4",
            work_dir=Path.cwd(),
            output_dir=tmp_path,
            run_id="language_case",
            resume=False,
            language="zh",
        )
    )

    method_inputs = [
        json.loads(line)
        for line in (tmp_path / "benchmark_runs" / "language_case" / "method_input.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert result["language"] == "zh"
    assert seen_languages == ["zh", "zh"]
    assert {item["language"] for item in method_inputs} == {"zh"}


def test_detect_graph_language_for_kg_and_tkg():
    zh_kg = {
        "entities": [
            {"id": "E1", "name": "北京大学", "type": "组织"},
            {"id": "E2", "name": "北京市", "type": "地点"},
        ],
        "relations": [{"source": "E1", "relation": "位于", "target": "E2"}],
    }
    en_tkg = {
        "entities": [{"id": "US", "name": "United States", "type": "Country"}],
        "temporal_facts": [
            {
                "id": "TF1",
                "subject": "CLINTON",
                "relation": "president_of",
                "object": "US",
                "time": {"type": "interval", "start": "1993", "end": "2001"},
            }
        ],
    }

    assert detect_graph_language(zh_kg) == "zh"
    assert detect_graph_language(en_tkg) == "en"
    assert detect_graph_language({}) == "en"


def test_run_benchmark_auto_detects_kg_language(tmp_path, monkeypatch):
    graph_path = tmp_path / "zh_kg.json"
    graph_path.write_text(
        json.dumps(
            {
                "entities": [
                    {"id": "E1", "name": "张三", "type": "人物"},
                    {"id": "E2", "name": "清华大学", "type": "组织"},
                    {"id": "E3", "name": "北京市", "type": "地点"},
                ],
                "relations": [
                    {"source": "E1", "relation": "就读于", "target": "E2"},
                    {"source": "E2", "relation": "位于", "target": "E3"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seen_languages = []

    def normal_batch(**kwargs):
        seen_languages.extend(item.get("language") for item in kwargs["batch"])
        rows = _fake_method_output(kwargs["batch"], kwargs["method"])
        return {"items": rows, "output_path": str(kwargs["output_path"]), "method": kwargs["method"]}

    monkeypatch.setattr("kgagent.benchmark.orchestrator._run_one_method_batch", normal_batch)

    result = asyncio.run(
        run_benchmark(
            data=str(graph_path),
            graph_type="KG",
            task="KGQA",
            method="sgsh_prompt",
            sample_count=1,
            model="gpt-5.4",
            work_dir=Path.cwd(),
            output_dir=tmp_path,
            run_id="auto_language_case",
            resume=False,
            language="auto",
        )
    )

    assert result["language"] == "zh"
    assert seen_languages == ["zh"]


def test_run_benchmark_rejects_hidden_methods(tmp_path):
    with pytest.raises(ValueError, match="Enabled methods"):
        asyncio.run(
            run_benchmark(
                data="examples/kg_benchmark_input.json",
                method="r2dqg_prompt",
                output_dir=tmp_path,
            )
        )


def test_tkg_rejects_static_methods(tmp_path):
    with pytest.raises(ValueError, match="Enabled methods: chronoqg"):
        asyncio.run(
            run_benchmark(
                data="examples/tkg_benchmark_input.json",
                graph_type="TKG",
                method="sgsh_prompt",
                output_dir=tmp_path,
            )
        )


def test_temporal_benchmark_keeps_supporting_nodes_edges(tmp_path, monkeypatch):
    def fake_chronoqg(**kwargs):
        item = {
            "sample_id": "chronoqg_000000",
            "generated_question": "When was Bill Clinton president of the United States?",
            "answer": {"text": "1993-01-20 to 2001-01-20", "type": "time", "id": "TF1"},
            "subgraph": {
                "nodes": [
                    {"id": "CLINTON", "name": "Bill Clinton", "type": "Person"},
                    {"id": "US", "name": "United States", "type": "Country"},
                ],
                "edges": [
                    {
                        "id": "TF1",
                        "source": "CLINTON",
                        "relation": "president_of",
                        "target": "US",
                        "time": {"type": "interval", "start": "1993-01-20", "end": "2001-01-20"},
                    }
                ],
                "facts": [
                    {
                        "id": "TF1",
                        "subject": "CLINTON",
                        "relation": "president_of",
                        "object": "US",
                        "time": {"type": "interval", "start": "1993-01-20", "end": "2001-01-20"},
                    }
                ],
            },
            "constraints": {"temporal_question_type": "time", "hop": 1, "difficulty": "easy"},
            "source": {"graph_id": "demo", "sample_strategy": "chronoqg"},
            "graph_type": "TKG",
            "method": "chronoqg",
            "metadata": {},
        }
        return {
            "items": [item],
            "output_path": str(kwargs["method_output_path"]),
            "method": "chronoqg",
            "stats": {"total": 1, "success": 1, "errors": 0},
        }

    monkeypatch.setattr("kgagent.benchmark.orchestrator._run_chronoqg_method", fake_chronoqg)

    result = asyncio.run(
        run_benchmark(
            data="examples/tkg_benchmark_input.json",
            graph_type="TKG",
            task="KGQA",
            method="chronoqg",
            sample_count=1,
            model="gpt-5.4",
            work_dir=Path.cwd(),
            output_dir=tmp_path,
            run_id="temporal_support_case",
            resume=False,
        )
    )

    rows = [json.loads(line) for line in Path(result["output_path"]).read_text(encoding="utf-8").splitlines()]
    supporting_graph = rows[0]["supporting_graph"]
    assert supporting_graph["facts"]
    assert supporting_graph["nodes"]
    assert supporting_graph["edges"]
    assert rows[0]["quality"]["valid"] is True


def test_sgsh_batch_failure_uses_valid_fallback(tmp_path, monkeypatch):
    from kgagent.benchmark.methods import sgsh_prompt_adapter

    def fail_chat_json(**kwargs):
        raise RuntimeError("simulated llm failure")

    monkeypatch.setattr(sgsh_prompt_adapter, "chat_json", fail_chat_json)

    result = sgsh_prompt_adapter.run_sgsh_prompt(
        [
            {
                "sample_id": "temporal_sample_000000",
                "task": "temporal_KGQA",
                "graph_type": "TKG",
                "temporal_subgraph": {
                    "nodes": [
                        {"id": "CLINTON", "name": "Bill Clinton", "type": "Person"},
                        {"id": "US", "name": "United States", "type": "Country"},
                    ],
                    "edges": [
                        {
                            "id": "TF1",
                            "source": "CLINTON",
                            "relation": "president_of",
                            "target": "US",
                            "time": {"type": "interval", "start": "1993-01-20", "end": "2001-01-20"},
                        }
                    ],
                    "facts": [
                        {
                            "id": "TF1",
                            "subject": "CLINTON",
                            "relation": "president_of",
                            "object": "US",
                            "time": {"type": "interval", "start": "1993-01-20", "end": "2001-01-20"},
                        }
                    ],
                },
                "answer": {"text": "1993-01-20 to 2001-01-20", "type": "time", "id": "TF1"},
                "constraints": {"temporal_question_type": "time", "hop": 1, "difficulty": "easy"},
                "source": {"graph_id": "demo", "sample_strategy": "temporal_fact"},
            }
        ],
        tmp_path / "sgsh.jsonl",
        model="gpt-5.4",
        base_url="http://localhost:3000/v1",
        api_key="sk-test",
    )

    row = result["items"][0]
    assert row["generated_question"].endswith("?")
    assert not row["generated_question"].startswith("[error:")
    assert row["metadata"]["fallback_used"] is True


def test_sgsh_prompt_includes_requested_language(tmp_path, monkeypatch):
    from kgagent.benchmark.methods import sgsh_prompt_adapter

    prompts = []
    system_prompts = []

    def fake_chat_json(**kwargs):
        prompts.append(kwargs["user_prompt"])
        system_prompts.append(kwargs["system_prompt"])
        if "Skeleton:" in kwargs["user_prompt"] or "问题骨架：" in kwargs["user_prompt"]:
            return {"question": "爱丽丝在哪里工作？", "notes": ""}
        return {"skeleton": "_在哪里工作？", "rationale": ""}

    monkeypatch.setattr(sgsh_prompt_adapter, "chat_json", fake_chat_json)

    result = sgsh_prompt_adapter.run_sgsh_prompt(
        [
            {
                "sample_id": "sample_zh",
                "task": "KGQA",
                "graph_type": "KG",
                "language": "zh",
                "subgraph": {
                    "nodes": [
                        {"id": "ALICE", "name": "Alice", "type": "Person"},
                        {"id": "ACME", "name": "Acme", "type": "Company"},
                    ],
                    "edges": [{"source": "ALICE", "relation": "works_at", "target": "ACME"}],
                },
                "answer": {"text": "Acme", "type": "entity", "id": "ACME"},
                "constraints": {"hop": 1, "difficulty": "easy"},
            }
        ],
        tmp_path / "sgsh_zh.jsonl",
        model="gpt-5.4",
        base_url="http://localhost:3000/v1",
        api_key="sk-test",
    )

    assert any("Output language: Chinese" in prompt for prompt in prompts)
    assert any("自然语言问题" in prompt for prompt in system_prompts)
    assert result["items"][0]["generated_question"] == "爱丽丝在哪里工作？"


def test_role_agent_uses_chinese_prompt(tmp_path, monkeypatch):
    from kgagent.benchmark.methods import prompt_methods

    seen = {}

    def fake_chat_json(**kwargs):
        seen["system_prompt"] = kwargs["system_prompt"]
        seen["user_prompt"] = kwargs["user_prompt"]
        return {"question": "爱丽丝在哪里工作？", "review_notes": ""}

    monkeypatch.setattr(prompt_methods, "chat_json", fake_chat_json)

    result = prompt_methods.run_prompt_method(
        [
            {
                "sample_id": "role_zh",
                "task": "KGQA",
                "graph_type": "KG",
                "language": "zh",
                "subgraph": {
                    "nodes": [
                        {"id": "ALICE", "name": "Alice", "type": "Person"},
                        {"id": "ACME", "name": "Acme", "type": "Company"},
                    ],
                    "edges": [{"source": "ALICE", "relation": "works_at", "target": "ACME"}],
                },
                "answer": {"text": "Acme", "type": "entity", "id": "ACME"},
                "constraints": {"hop": 1, "difficulty": "easy"},
            }
        ],
        tmp_path / "role_zh.jsonl",
        method="role_agent_qg",
        model="gpt-5.4",
        base_url="http://localhost:3000/v1",
        api_key="sk-test",
    )

    assert "协作式编委团队" in seen["system_prompt"]
    assert "Output language: Chinese" in seen["user_prompt"]
    assert result["items"][0]["generated_question"] == "爱丽丝在哪里工作？"


def test_chronoqg_prompt_tables_include_chinese_templates():
    from kgagent.benchmark.methods.chronoqg.chrono_qg import prompts

    assert prompts.REWRITE_PROMPTS["zh"] != prompts.REWRITE_PROMPTS["en"]
    assert "中文问题" in prompts.REWRITE_PROMPTS["zh"]
    assert "修正后的中文问题" in prompts.REWRITE_FIX_PROMPTS["zh"]


def test_chronoqg_adapter_resumes_internal_stages(tmp_path, monkeypatch):
    from kgagent.benchmark.methods import chronoqg_adapter

    calls = {"sample": 0, "build": 0, "verify": 0}

    def fail_stage(name):
        def _inner(*args, **kwargs):
            calls[name] += 1
            raise AssertionError(f"{name} should be skipped")

        return _inner

    fake_main = types.ModuleType("main")
    fake_main._run_sample = fail_stage("sample")
    fake_main._run_build = fail_stage("build")
    fake_main._run_verify = fail_stage("verify")

    class FakePipelineConfig:
        def __init__(self, output_dir):
            self.output_dir = output_dir

        @classmethod
        def load(cls, path):
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(Path(raw["output_dir"]))

    fake_config = types.ModuleType("tkgqg_config")
    fake_config.PipelineConfig = FakePipelineConfig
    monkeypatch.setitem(sys.modules, "main", fake_main)
    monkeypatch.setitem(sys.modules, "tkgqg_config", fake_config)

    def fake_convert_tkg_to_chronoqg_format(*, tkg, output_dir, time_granularity):
        output_dir.mkdir(parents=True, exist_ok=True)
        kg_file = output_dir / "full.txt"
        entity_map = output_dir / "entities.txt"
        relation_map = output_dir / "relations.txt"
        kg_file.write_text("张三\t就读于\t清华大学\t2020\t2024\n", encoding="utf-8")
        entity_map.write_text("张三\t张三\n清华大学\t清华大学\n", encoding="utf-8")
        relation_map.write_text("就读于\t就读于\n", encoding="utf-8")
        return {"kg_file": kg_file, "entity_map": entity_map, "relation_map": relation_map}

    monkeypatch.setattr(
        "kgagent.benchmark.tools.chronoqg_converter.convert_tkg_to_chronoqg_format",
        fake_convert_tkg_to_chronoqg_format,
    )
    monkeypatch.setattr("kgagent.benchmark.tools.chronoqg_converter.estimate_time_granularity", lambda tkg: "year")

    output_dir = tmp_path / "chronoqg"
    chronoqg_output = output_dir / "chronoqg_output"
    trace_path = chronoqg_output / "traces" / "trace_samples.jsonl"
    benchmark_path = chronoqg_output / "benchmark" / "benchmark_tc1.jsonl"
    verified_path = chronoqg_output / "verified" / "tc1" / "dataset.jsonl"
    trace_path.parent.mkdir(parents=True)
    benchmark_path.parent.mkdir(parents=True)
    verified_path.parent.mkdir(parents=True)
    trace_path.write_text(json.dumps({"id": "trace_1"}, ensure_ascii=False) + "\n", encoding="utf-8")
    benchmark_path.write_text(json.dumps({"benchmark_id": "bench_1"}, ensure_ascii=False) + "\n", encoding="utf-8")
    verified_path.write_text(
        json.dumps(
            {
                "benchmark_id": "bench_1",
                "gold_question": "张三什么时候就读于清华大学？",
                "answer": "2020 to 2024",
                "facts": [
                    {
                        "id": "TF1",
                        "subject": "张三",
                        "relation": "就读于",
                        "object": "清华大学",
                        "time": {"type": "interval", "start": "2020", "end": "2024"},
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = chronoqg_adapter.run_chronoqg(
        {
            "entities": [
                {"id": "张三", "name": "张三"},
                {"id": "清华大学", "name": "清华大学"},
            ],
            "temporal_facts": [],
        },
        output_dir,
        model="gpt-5.4",
        base_url="http://localhost:3000/v1",
        api_key="sk-test",
        language="zh",
        resume=True,
    )

    assert calls == {"sample": 0, "build": 0, "verify": 0}
    assert result["stats"]["skipped_stages"] == ["sample", "build", "verify"]
    assert result["items"][0]["generated_question"] == "张三什么时候就读于清华大学？"
