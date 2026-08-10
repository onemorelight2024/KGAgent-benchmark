"""Reasoning route for KGAgent."""

from kgagent.reasoning.QA.manager import (
    list_reasoning_methods,
    recommend_reasoning_methods,
    run_graphrag_qa_for_agent,
    run_rag_anything_qa_for_agent,
    run_rag_anything_qa_for_agent_async,
)
from kgagent.reasoning.reasoning_entry import ReasoningEntry

__all__ = [
    "ReasoningEntry",
    "list_reasoning_methods",
    "recommend_reasoning_methods",
    "run_graphrag_qa_for_agent",
    "run_rag_anything_qa_for_agent",
    "run_rag_anything_qa_for_agent_async",
]
