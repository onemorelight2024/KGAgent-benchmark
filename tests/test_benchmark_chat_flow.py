from pathlib import Path

import pytest

from kgagent.benchmark.chat_flow import BenchmarkChatFlow, looks_like_benchmark_request


@pytest.fixture(autouse=True)
def clear_benchmark_api_env(monkeypatch):
    for name in (
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "OPENAI_API_KEY",
        "LLM_BASE_URL",
        "DF_API_URL",
        "DF_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_benchmark_chat_flow_collects_static_sgsh_params():
    workspace = Path.cwd()
    flow = BenchmarkChatFlow(workspace=workspace)

    message, params = flow.start("我想抽取KG相关任务的benchmark")
    assert params is None
    assert "两种类型" in message

    message, params = flow.handle("1")
    assert params is None
    assert "KGQA" in message

    message, params = flow.handle("做KGQA的benchmark，我的图谱在 examples/kg_benchmark_input.json")
    assert params is None
    assert "多少个" in message

    message, params = flow.handle("5个")
    assert params is None
    assert "SGSH" in message

    message, params = flow.handle("D")
    assert params is None
    assert "base_url" in message

    message, params = flow.handle("http://localhost:3000/v1，key为sk-test，model用gpt-4o-mini")
    assert params is not None
    assert params["graph_type"] == "KG"
    assert params["task"] == "KGQA"
    assert params["data"] == "examples/kg_benchmark_input.json"
    assert params["sample_count"] == 5
    assert params["method"] == "sgsh_prompt"
    assert params["base_url"] == "http://localhost:3000/v1"
    assert params["api_key"] == "sk-test"
    assert "sk-test" not in message


def test_benchmark_request_detection():
    assert looks_like_benchmark_request("我想做一个KG相关的benchmark")
    assert looks_like_benchmark_request("帮我生成评测数据集")


def test_benchmark_chat_flow_accepts_method_a():
    flow = BenchmarkChatFlow(workspace=Path.cwd())
    flow.start("我想做benchmark")
    flow.handle("1")
    flow.handle("KGQA，examples/kg_benchmark_input.json")
    flow.handle("1个")
    message, params = flow.handle("A")

    assert params is None
    assert "base_url" in message
    assert flow.method == "role_agent_qg"


def test_benchmark_chat_flow_accepts_skeleton_method_in_chinese():
    flow = BenchmarkChatFlow(workspace=Path.cwd())
    flow.start("我想做benchmark")
    flow.handle("1")
    flow.handle("KGQA，examples/kg_benchmark_input.json")
    flow.handle("5个")
    message, params = flow.handle("骨架法吧")

    assert params is None
    assert "base_url" in message
    assert flow.method == "sgsh_prompt"


def test_temporal_flow_defaults_to_chronoqg_when_asked_best_method():
    flow = BenchmarkChatFlow(workspace=Path.cwd())
    flow.start("我想做时序benchmark")
    flow.handle("KGQA，examples/tkg_benchmark_input.json")
    flow.handle("1个")
    message, params = flow.handle("选择最合适的方法")

    assert params is None
    assert "base_url" in message
    assert flow.method == "chronoqg"


def test_flow_understands_chinese_path_prefix_and_config_file():
    flow = BenchmarkChatFlow(workspace=Path.cwd())
    flow.start("我想弄一个KG相关任务的benchmark")
    flow.handle("1")
    flow.handle("kgqa")

    message, params = flow.handle("在examples/kg_benchmark_input.json")
    assert params is None
    assert flow.input_path == "examples/kg_benchmark_input.json"
    assert "多少个" in message

    flow.handle("5")
    flow.handle("d")
    message, params = flow.handle("/home/liuxuem/config.md里面有")

    assert params is not None
    assert params["base_url"]
    assert params["api_key"]
    assert "sk-" not in message or "sk-..." in message


def test_flow_uses_df_api_environment(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DF_API_URL", "http://localhost:3000/v1")
    monkeypatch.setenv("DF_API_KEY", "sk-test-env-key")

    flow = BenchmarkChatFlow(workspace=Path.cwd())
    flow.start("我想弄一个KG相关任务的benchmark")
    flow.handle("1")
    flow.handle("kgqa")
    flow.handle("在examples/kg_benchmark_input.json")
    flow.handle("5")
    message, params = flow.handle("骨架法吧")

    assert params is not None
    assert params["base_url"] == "http://localhost:3000/v1"
    assert params["api_key"] == "sk-test-env-key"
    assert "sk-test-env-key" not in message
