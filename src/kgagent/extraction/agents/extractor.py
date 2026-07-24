"""Extraction Agent - directly performs KG extraction."""

from __future__ import annotations

from claude_agent_sdk import AgentDefinition

from kgagent.system.prompts import load_prompt, EXTRACTION_PROMPT


def build_extraction_agent() -> AgentDefinition:
    """Build the extraction agent.

    This agent directly performs knowledge graph extraction based on user requests.
    It handles:
    - Relation triples: Standard entity-relation-entity triples
    - Temporal quadruples: Temporal facts with time annotations
    - Hyper-relations: Relations with structured attributes
    """
    return AgentDefinition(
        description=(
            "Directly extracts knowledge graphs from text or JSON. "
            "Handles relation triples, temporal quadruples, and hyper-relations."
        ),
        prompt=load_prompt(EXTRACTION_PROMPT),
        tools=[],  # No tools needed - agent performs extraction directly
        maxTurns=10,
    )
