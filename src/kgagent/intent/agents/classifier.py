"""Intent classification agent definition."""

from claude_agent_sdk import AgentDefinition
from kgagent.system.prompts import load_prompt


def build_intent_agent() -> AgentDefinition:
    """Build intent classification agent.

    Returns:
        AgentDefinition for intent classification
    """
    prompt = load_prompt("intent_classification")

    return AgentDefinition(
        prompt=prompt,
        description="Intent classification agent for parsing user intent",
        tools=[],  # Intent classification doesn't need tools
    )
