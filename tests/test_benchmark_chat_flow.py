from pathlib import Path
import asyncio

import pytest

from kgagent.benchmark.agents.chat_flow import BenchmarkChatFlow, looks_like_benchmark_request
from kgagent.benchmark.agents.conversation import BenchmarkConversation
from kgagent.core.language import detect_language


@pytest.fixture(autouse=True)
def clear_benchmark_api_env(monkeypatch):
    for name in (
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "OPENAI_API_KEY",
        "LLM_BASE_URL",
        "DF_API_URL",
        "DF_API_KEY",
        "KG_API_URL",
        "KG_API_KEY",
        "KG_MODEL",
        "KG_BENCHMARK_MODEL",
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
    assert params is not None
    assert params["graph_type"] == "KG"
    assert params["task"] == "KGQA"
    assert params["data"] == "examples/kg_benchmark_input.json"
    assert params["sample_count"] == 5
    assert params["method"] == "sgsh_prompt"
    assert params["language"] is None
    assert "sk-test" not in message


def test_benchmark_request_detection():
    assert looks_like_benchmark_request("我想做一个KG相关的benchmark")
    assert looks_like_benchmark_request("帮我生成评测数据集")


def test_detect_language_uses_fifteen_percent_chinese_threshold():
    assert detect_language("中文abcde") == "zh"
    assert detect_language("中abcdef") == "en"


def test_benchmark_chat_flow_accepts_method_b():
    flow = BenchmarkChatFlow(workspace=Path.cwd())
    flow.start("我想做benchmark")
    flow.handle("1")
    flow.handle("KGQA，examples/kg_benchmark_input.json")
    flow.handle("1个")
    message, params = flow.handle("B")

    assert params is not None
    assert params["method"] == "role_agent_qg"
    assert flow.method == "role_agent_qg"


def test_benchmark_chat_flow_explicit_method_beats_defaults():
    flow = BenchmarkChatFlow(workspace=Path.cwd())

    assert flow._extract_method("RoleAgentQG，其他默认") == "role_agent_qg"
    assert flow._extract_method("role agent, defaults for the rest") == "role_agent_qg"
    assert flow._extract_method("多角色编审，其他默认") == "role_agent_qg"


def test_benchmark_chat_flow_accepts_skeleton_method_in_chinese():
    flow = BenchmarkChatFlow(workspace=Path.cwd())
    flow.start("我想做benchmark")
    flow.handle("1")
    flow.handle("KGQA，examples/kg_benchmark_input.json")
    flow.handle("5个")
    message, params = flow.handle("骨架法吧")

    assert params is not None
    assert params["method"] == "sgsh_prompt"
    assert flow.method == "sgsh_prompt"


def test_temporal_flow_defaults_to_chronoqg():
    flow = BenchmarkChatFlow(workspace=Path.cwd())
    flow.start("我想做时序benchmark")
    flow.handle("KGQA，examples/tkg_benchmark_input.json")
    message, params = flow.handle("1个")

    assert params is not None
    assert params["method"] == "chronoqg"
    assert flow.method == "chronoqg"


def test_temporal_flow_accepts_chronoqg_method():
    flow = BenchmarkChatFlow(workspace=Path.cwd())

    assert flow._extract_method("ChronoQG，其他默认") == "chronoqg"
    assert flow._extract_method("chrono qg, defaults for the rest") == "chronoqg"


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
    assert "sk-" not in message or "sk-..." in message


def test_flow_does_not_require_method_api_environment(monkeypatch):
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
    assert params["base_url"] is None
    assert params["api_key"] is None
    assert "sk-test-env-key" not in message


def test_flow_parses_run_id_resume_and_batch_size(monkeypatch):
    monkeypatch.setenv("KG_API_URL", "http://localhost:3000/v1")
    monkeypatch.setenv("KG_API_KEY", "sk-test-env-key")

    flow = BenchmarkChatFlow(workspace=Path.cwd())
    flow.start("I want to generate a KGQA benchmark")
    flow.handle("static KG")
    flow.handle("examples/kg_benchmark_input.json")
    flow.handle("2")
    flow.handle("SGSH")
    message, params = flow.handle("run_id is en_case, fresh run, model gpt-5.4, batch 2")

    assert params is not None
    assert params["run_id"] == "en_case"
    assert params["resume"] is False
    assert params["batch_size"] == 2
    assert params["model"] == "gpt-5.4"
    assert params["language"] is None
    assert "sk-test-env-key" not in message


def test_benchmark_chat_flow_parses_explicit_output_language():
    flow = BenchmarkChatFlow(workspace=Path.cwd())
    message, params = flow.start("我想做一个普通KGQA benchmark")
    assert params is None
    assert flow.language is None

    flow.handle("examples/kg_benchmark_input.json")
    message, params = flow.handle("2，生成中文问题")
    assert params is None
    assert "SGSH" in message
    assert "请选择" in message

    message, params = flow.handle("A")
    assert params is not None
    assert flow.language == "zh"


def test_benchmark_conversation_keeps_llm_reply(monkeypatch):
    conversation = BenchmarkConversation(workspace=Path.cwd(), sdk_model="test-model")

    async def fake_ask_agent(user_input):
        return {
            "reply": "Which graph type do you want?",
            "updates": {},
            "ready_to_run": False,
        }

    monkeypatch.setattr(conversation, "_ask_agent", fake_ask_agent)

    message, params = asyncio.run(conversation.start("I want to generate a benchmark"))
    assert params is None
    assert message == "Which graph type do you want?"


def test_benchmark_conversation_keeps_llm_method_reply(monkeypatch):
    monkeypatch.setenv("DF_API_URL", "http://localhost:3000/v1")
    monkeypatch.setenv("DF_API_KEY", "sk-test-env-key")
    conversation = BenchmarkConversation(workspace=Path.cwd(), sdk_model="test-model")

    async def fake_ask_agent(user_input):
        return {
            "reply": "LLM method menu reply",
            "updates": {
                "graph_type": "KG",
                "task": "KGQA",
                "input_path": "examples/kg_benchmark_input.json",
                "sample_count": 5,
            },
            "ready_to_run": False,
        }

    monkeypatch.setattr(conversation, "_ask_agent", fake_ask_agent)

    message, params = asyncio.run(
        conversation.start("我想做静态 KG 的 KGQA benchmark，文件在 examples/kg_benchmark_input.json，5个")
    )
    assert params is None
    assert message == "LLM method menu reply"


def test_benchmark_conversation_ready_overrides_stale_llm_reply(monkeypatch):
    monkeypatch.setenv("DF_API_URL", "http://localhost:3000/v1")
    monkeypatch.setenv("DF_API_KEY", "sk-test-env-key")
    conversation = BenchmarkConversation(workspace=Path.cwd(), sdk_model="test-model")
    conversation.flow.graph_type = "KG"
    conversation.flow.task = "KGQA"
    conversation.flow.input_path = "examples/kg_benchmark_input.json"
    conversation.flow.sample_count = 5

    async def fake_ask_agent(user_input):
        return {
            "reply": "你是要直接开始跑，还是需要先补充/检查 API 配置？",
            "updates": {"method": "sgsh_prompt"},
            "ready_to_run": False,
        }

    monkeypatch.setattr(conversation, "_ask_agent", fake_ask_agent)

    message, params = asyncio.run(conversation.handle("A"))
    assert params is not None
    assert "信息齐全，开始生成 benchmark" in message
    assert "补充/检查 API 配置" not in message
    assert params["method"] == "sgsh_prompt"


def test_benchmark_conversation_english_fallback_when_llm_reply_missing(monkeypatch):
    conversation = BenchmarkConversation(workspace=Path.cwd(), sdk_model="test-model")

    async def fake_ask_agent(user_input):
        return {
            "reply": "",
            "updates": {"graph_type": "KG", "task": "KGQA"},
            "ready_to_run": False,
        }

    monkeypatch.setattr(conversation, "_ask_agent", fake_ask_agent)

    message, params = asyncio.run(conversation.start("I want to generate a KGQA benchmark, static KG"))
    assert params is None
    assert "Please provide the graph JSON file path" in message
    assert conversation.flow.language is None


def test_benchmark_conversation_keeps_english_with_path_when_ready(monkeypatch):
    conversation = BenchmarkConversation(workspace=Path.cwd(), sdk_model="test-model")

    async def fake_ask_agent(user_input):
        return {
            "reply": "",
            "updates": {
                "graph_type": "KG",
                "task": "KGQA",
                "input_path": "examples/kg_benchmark_resume_sgsh_en.json",
                "sample_count": 2,
            },
            "ready_to_run": False,
        }

    monkeypatch.setattr(conversation, "_ask_agent", fake_ask_agent)

    message, params = asyncio.run(
        conversation.start(
            "I want to generate a static KG KGQA benchmark from "
            "examples/kg_benchmark_resume_sgsh_en.json, generate 2 questions"
        )
    )
    assert params is not None
    assert conversation.current_reply_language() == "en"
    assert "All required information is ready" in message
    assert "信息齐全" not in message


def test_benchmark_conversation_keeps_chinese_with_tkg_and_path(monkeypatch):
    conversation = BenchmarkConversation(workspace=Path.cwd(), sdk_model="test-model")

    async def fake_ask_agent(user_input):
        return {
            "reply": "",
            "updates": {
                "graph_type": "TKG",
                "task": "KGQA",
                "input_path": "examples/tkg_benchmark_resume_chronoqg_zh.json",
                "sample_count": 2,
            },
            "ready_to_run": True,
        }

    monkeypatch.setattr(conversation, "_ask_agent", fake_ask_agent)

    message, params = asyncio.run(
        conversation.start(
            "我想做一个时序 TKG 的 KGQA benchmark，我的文件路径在"
            "examples/tkg_benchmark_resume_chronoqg_zh.json，我要生成2个问题"
        )
    )
    assert params is not None
    assert conversation.current_reply_language() == "zh"
    assert "信息齐全，开始生成 benchmark" in message
    assert "All required information is ready" not in message
