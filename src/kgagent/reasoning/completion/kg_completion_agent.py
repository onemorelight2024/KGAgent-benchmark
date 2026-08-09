from __future__ import annotations

from claude_agent_sdk import AgentDefinition

from kgagent.prompts import load_prompt


def build_kg_completion_agent() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Use this agent when the user wants KG completion, link prediction, "
            "missing head prediction, or missing tail prediction over an existing KG."
        ),
        prompt=load_prompt("kg_completion.md"),
        tools=[
            "mcp__kgagent__load_json_input",
            "mcp__kgagent__list_kg_completion_methods",
            "mcp__kgagent__recommend_kg_completion_methods",
            "mcp__kgagent__run_kicgpt_kg_completion",
            "mcp__kgagent__validate_kg_completion",
            "mcp__kgagent__save_result",
        ],
        model="sonnet",
        maxTurns=10,
    )
