from __future__ import annotations

from claude_agent_sdk import AgentDefinition

from kgagent.prompts import load_prompt


def build_qa_reasoning_agent() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Use this agent when the user wants question answering over documents, "
            "existing RAG/KG storage, or multimodal document content."
        ),
        prompt=load_prompt("qa_reasoning.md"),
        tools=[
            "mcp__kgagent__list_reasoning_methods",
            "mcp__kgagent__recommend_reasoning_methods",
            "mcp__kgagent__run_rag_anything_qa",
            "mcp__kgagent__run_graphrag_qa",
            "mcp__kgagent__validate_qa_reasoning",
            "mcp__kgagent__save_result",
        ],
        model="sonnet",
        maxTurns=10,
    )
