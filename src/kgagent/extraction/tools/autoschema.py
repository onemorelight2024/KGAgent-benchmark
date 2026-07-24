"""AutoSchemaKG tool for event knowledge graph extraction."""

from __future__ import annotations

from typing import Any


# AutoSchemaKG 三阶段 prompt
AUTOSCHEMA_PROMPTS = {
    "en": {
        "system": "You are a helpful assistant who always response in a valid array of JSON objects without any explanation",

        "entity_relation": """Given a passage, summarize all the important entities and the relations between them in a concise manner. Relations should briefly capture the connections between entities, without repeating information from the head and tail entities. The entities should be as specific as possible. Exclude pronouns from being considered as entities.
        You must **strictly output in the following JSON format**:
        [
            {
                "Head": "{a noun}",
                "Relation": "{a verb}",
                "Tail": "{a noun}"
            }
        ]
        Here is the passage:""",

        "event_entity": """Please analyze and summarize the participation relations between the events and entities in the given paragraph. Each event is a single independent sentence. Additionally, identify all the entities that participated in the events. Do not use ellipses.
        You must **strictly output in the following JSON format**:
        [
            {
                "Event": "{a simple sentence describing an event}",
                "Entity": ["entity 1", "entity 2", "..."]
            }
        ]
        Here is the passage:""",

        "event_relation": """Please analyze and summarize the relationships between the events in the paragraph. Each event is a single independent sentence. Identify temporal and causal relationships between the events using the following types: before, after, at the same time, because, and as a result. Each extracted triple should be specific, meaningful, and able to stand alone. Do not use ellipses.
        You must **strictly output in the following JSON format**:
        [
            {
                "Head": "{a simple sentence describing the event 1}",
                "Relation": "{temporal or causality relation between the events}",
                "Tail": "{a simple sentence describing the event 2}"
            }
        ]
        Here is the passage:""",
    },
    "zh": {
        "system": "你是一个始终以有效JSON数组格式回应的助手",

        "entity_relation": """给定一段文字，提取所有重要实体及其关系，并以简洁的方式总结。关系描述应清晰表达实体间的联系，且不重复头尾实体的信息。实体需具体明确，排除代词。
        返回格式必须为以下JSON结构,内容需用简体中文表述:
        [
            {
                "Head": "{名词}",
                "Relation": "{动词或关系描述}",
                "Tail": "{名词}"
            }
        ]
        给定以下段落：""",

        "event_entity": """分析段落中的事件及其参与实体。每个事件应为独立单句，列出所有相关实体（需具体，不含代词）。
        返回格式必须为以下JSON结构,内容需用简体中文表述:
        [
            {
                "Event": "{描述事件的简单句子}",
                "Entity": ["实体1", "实体2", "..."]
            }
        ]
        给定以下段落：""",

        "event_relation": """分析事件间的时序或因果关系,关系类型包括:之前,之后,同时,因为,结果.每个事件应为独立单句。
        返回格式必须为以下JSON结构.内容需用简体中文表述.
        [
            {
                "Head": "{事件1描述}",
                "Relation": "{时序/因果关系}",
                "Tail": "{事件2描述}"
            }
        ]
        给定以下段落：""",
    }
}


# AutoSchemaKG JSON Schema
AUTOSCHEMA_SCHEMA = {
    "entity_relation": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "Head": {"type": "string"},
                "Relation": {"type": "string"},
                "Tail": {"type": "string"}
            },
            "required": ["Head", "Relation", "Tail"],
        }
    },
    "event_entity": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "Event": {"type": "string"},
                "Entity": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            },
            "required": ["Event", "Entity"],
        }
    },
    "event_relation": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "Head": {"type": "string"},
                "Relation": {"type": "string"},
                "Tail": {"type": "string"}
            },
            "required": ["Head", "Relation", "Tail"],
        }
    }
}


def extract_autoschema_kg(text: str, language: str = "en") -> dict[str, Any]:
    """Extract AutoSchemaKG-style event KG from text.

    This is a simplified wrapper that returns the three-stage prompts
    and schemas. The actual extraction will be done by the LLM.

    Args:
        text: Input text
        language: Language code ("en" or "zh")

    Returns:
        Dict with prompts for three stages
    """
    prompts = AUTOSCHEMA_PROMPTS.get(language, AUTOSCHEMA_PROMPTS["en"])

    return {
        "stages": ["entity_relation", "event_entity", "event_relation"],
        "prompts": {
            "system": prompts["system"],
            "entity_relation": prompts["entity_relation"] + "\n\n" + text,
            "event_entity": prompts["event_entity"] + "\n\n" + text,
            "event_relation": prompts["event_relation"] + "\n\n" + text,
        },
        "schemas": AUTOSCHEMA_SCHEMA,
        "text": text,
    }


def format_autoschema_result(
    entity_relations: list[dict],
    event_entities: list[dict],
    event_relations: list[dict]
) -> dict[str, Any]:
    """Format AutoSchemaKG extraction results.

    Args:
        entity_relations: Entity-entity triples
        event_entities: Event-entity pairs
        event_relations: Event-event triples

    Returns:
        Formatted result with all three types
    """
    return {
        "entity_relation_dict": entity_relations,
        "event_entity_relation_dict": event_entities,
        "event_relation_dict": event_relations,
    }
