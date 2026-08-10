"""Intent classification entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
)

from kgagent.extraction.config import ExtractionConfig
from kgagent.intent.agents.classifier import build_intent_agent

logger = logging.getLogger(__name__)

_VALID_INTENTS = {
    "chat",
    "extract",
    "reason",
    "help",
    "command",
    "save",
    "convert",
    "import",
    "parse",
}
_QA_METHOD_INTENTS = {"rag_anything", "graphrag"}
_PATH_PATTERN = (
    r"([A-Za-z]:\\[^\s:]+(?:\.[A-Za-z0-9]+)?|"
    r"[^\s]+(?:\.json|\.jsonl|\.txt|\.md|\.pdf|\.docx?|\.pptx?|\.xlsx?|"
    r"\.png|\.jpg|\.jpeg|\.graphml|\.xml|\.dump))"
)


class IntentEntry:
    """Entry point for intent classification."""

    def __init__(self, config: ExtractionConfig):
        self.config = config
        self.agent = build_intent_agent()

    def parse_intent(self, user_input: str) -> dict[str, Any]:
        """Synchronous intent parsing."""
        return asyncio.run(self.parse_intent_async(user_input))

    async def parse_intent_async(
        self,
        user_input: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Parse user intent from natural language input."""
        logger.info("Parsing intent from: %s...", user_input[:50])

        if user_input.startswith(":"):
            command = user_input[1:].strip()
            return {
                "intent": "command",
                "confidence": 1.0,
                "command": command,
                "explanation": "system command",
            }

        prompt = (
            "Classify the intent of this user input.\n"
            "Return exactly one JSON object and nothing else.\n\n"
            f"{user_input}"
        )
        if context:
            prompt = f"{context}\n\n---\n\n{prompt}"

        options = ClaudeAgentOptions(
            cwd=self.config.work_dir,
            model=self.config.model_name,
            system_prompt=self.agent.prompt,
            tools=[],
            permission_mode=self.config.permission_mode,
            max_turns=1,
        )

        result = None
        async with ClaudeSDKClient(options) as client:
            await client.query(prompt)

            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            result = self._parse_json_result(block.text)
                            if result is not None:
                                break
                if result is not None:
                    break

        if result is not None:
            result = self._normalize_result(result, user_input)

        if result is None:
            result = self._fallback_intent(user_input)

        if result is None:
            logger.warning("Intent classification failed, defaulting to chat")
            return {
                "intent": "chat",
                "confidence": 0.5,
                "response": (
                    "抱歉，我没有理解你的意思。你可以：\n"
                    "1. 直接输入文本让我抽取知识图谱\n"
                    "2. 输入 :help 查看帮助\n"
                    "3. 随意和我聊天"
                ),
            }

        logger.info(
            "Intent classified: %s (confidence: %s)",
            result["intent"],
            result.get("confidence", 0),
        )
        return result

    def _parse_json_result(self, text: str) -> dict[str, Any] | None:
        """Parse JSON result from agent response."""
        text = text.strip()

        json_blocks = re.findall(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if json_blocks:
            for block in reversed(json_blocks):
                try:
                    result = json.loads(block.strip())
                    if isinstance(result, dict) and "intent" in result:
                        logger.info("Parsed JSON from code block")
                        return result
                except json.JSONDecodeError:
                    continue

        try:
            result = json.loads(text)
            if isinstance(result, dict) and "intent" in result:
                return result
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                result = json.loads(text[start : end + 1])
                if isinstance(result, dict) and "intent" in result:
                    logger.info("Parsed JSON from text extraction")
                    return result
            except json.JSONDecodeError:
                pass

        logger.warning("Could not parse JSON from response: %s...", text[:200])
        return None

    def _normalize_result(self, result: dict[str, Any], user_input: str) -> dict[str, Any] | None:
        """Normalize model output into the intent schema expected by chat."""
        intent = str(result.get("intent", "")).strip().lower()
        if not intent:
            return None

        explicit_reason = _explicit_reasoning_override(user_input)
        if explicit_reason is not None:
            if intent != "reason":
                logger.info("Overriding classifier intent with explicit reasoning pattern")
            return explicit_reason

        if intent in _VALID_INTENTS:
            return result

        if intent in _QA_METHOD_INTENTS:
            params = dict(result.get("parameters") or {})
            detected_path, detected_questions_path = _extract_reasoning_paths(user_input)
            question = ""
            match = re.search(r"question(?:\s+from\s+.+?)?\s*:\s*(.+)$", user_input, re.IGNORECASE)
            if match:
                question = match.group(1).strip()
            elif ":" in user_input:
                question = user_input.split(":", 1)[1].strip()

            params.setdefault("task_type", "qa")
            params["method"] = intent
            params.setdefault("input_mode", "file" if detected_path else "auto")
            params.setdefault("input_path", detected_path)
            params.setdefault("questions_path", detected_questions_path)
            params.setdefault("data", None)
            params.setdefault("question", question)
            params.setdefault("query", None)
            params.setdefault("dataset", "")
            params.setdefault("metrics", [])
            params.setdefault("use_last_result", False)
            params.setdefault("use_last_file", False)
            params.setdefault("needs_confirmation", False)

            return {
                "intent": "reason",
                "confidence": result.get("confidence", 0.7),
                "parameters": params,
                "explanation": result.get("explanation", f"normalized from QA method '{intent}'"),
            }

        logger.warning("Unsupported intent value returned by classifier: %s", intent)
        return None

    def _fallback_intent(self, user_input: str) -> dict[str, Any] | None:
        """Heuristic fallback intent parser when LLM output is not valid JSON."""
        text = user_input.strip()
        lower = text.lower()

        detected_path, detected_questions_path = _extract_reasoning_paths(text)
        detected_path = detected_path or None
        detected_questions_path = detected_questions_path or None

        if any(word in lower for word in ["save", "保存", "导出"]):
            return {
                "intent": "save",
                "confidence": 0.75,
                "parameters": {
                    "target": "last",
                    "file_path": detected_path,
                },
                "explanation": "detected save request",
            }

        if detected_path and detected_path.lower().endswith((".dump", ".graphml", ".xml")):
            if any(word in lower for word in ["import", "load", "导入"]):
                return {
                    "intent": "import",
                    "confidence": 0.8,
                    "parameters": {
                        "input_path": detected_path,
                        "output_path": None,
                    },
                    "explanation": "detected import request",
                }

        if detected_path and detected_path.lower().endswith(
            (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg")
        ):
            if any(word in lower for word in ["parse", "markdown", "extract text", "pdf to text", "解析"]):
                return {
                    "intent": "parse",
                    "confidence": 0.8,
                    "parameters": {
                        "input_path": detected_path,
                        "output_path": None,
                    },
                    "explanation": "detected document parse request",
                }

        if any(word in lower for word in ["convert", "neo4j", "graphml", "rdf", "csv", "ttl", "转换", "格式"]):
            target_format = "neo4j_csv"
            if any(word in lower for word in ["graphml", ".graphml", ".xml"]):
                target_format = "graphml"
            elif any(word in lower for word in ["rdf", "ttl", "turtle"]):
                target_format = "rdf"
            elif "json" in lower:
                target_format = "json"

            return {
                "intent": "convert",
                "confidence": 0.76,
                "parameters": {
                    "source": f"file:{detected_path}" if detected_path else "last",
                    "target_format": target_format,
                    "output_path": None,
                },
                "explanation": "detected format conversion request",
            }

        reason_keywords = [
            "answer this question",
            "question answering",
            "based on the last extraction",
            "last kg",
            "complete the last kg",
            "link prediction",
            "missing head",
            "missing tail",
            "补全",
            "问答",
            "回答这个问题",
            "基于上一次结果",
        ]
        if any(word in lower for word in reason_keywords) or any(method in lower for method in _QA_METHOD_INTENTS):
            task_type = "qa"
            if any(word in lower for word in ["complete", "completion", "link prediction", "missing head", "missing tail", "补全"]):
                task_type = "completion"

            method = "auto"
            if "rag_anything" in lower:
                method = "rag_anything"
            elif "graphrag" in lower:
                method = "graphrag"

            question = ""
            if task_type == "qa":
                match = re.search(r"question(?:\s+from\s+.+?)?\s*:\s*(.+)$", text, re.IGNORECASE)
                if match:
                    question = match.group(1).strip()
                elif ":" in text:
                    question = text.split(":", 1)[1].strip()
                else:
                    question = text

            query = None
            if task_type == "completion":
                triplet_match = re.search(r"\(([^,]+),\s*([^,]+),\s*([^)]+)\)", text)
                if triplet_match:
                    query = {
                        "source": triplet_match.group(1).strip(),
                        "relation": triplet_match.group(2).strip(),
                        "target": triplet_match.group(3).strip(),
                    }

            return {
                "intent": "reason",
                "confidence": 0.8,
                "parameters": {
                    "task_type": task_type,
                    "method": method,
                    "input_mode": (
                        "last_extraction"
                        if "last extraction" in lower or "last kg" in lower
                        else "file"
                        if detected_path
                        else "auto"
                    ),
                    "input_path": detected_path or "",
                    "questions_path": detected_questions_path or "",
                    "data": None,
                    "question": question,
                    "query": query,
                    "dataset": "",
                    "metrics": [],
                    "use_last_result": "last extraction" in lower or "last kg" in lower,
                    "use_last_file": "last file" in lower,
                    "needs_confirmation": task_type != "qa",
                },
                "explanation": "detected reasoning request",
            }

        if any(
            word in lower
            for word in ["extract", "triples", "triple", "temporal", "hyper", "event", "抽取", "三元组", "知识图谱"]
        ):
            extraction_type = "auto"
            if "temporal" in lower or "时序" in lower:
                extraction_type = "temporal"
            elif "hyper" in lower or "超关系" in lower:
                extraction_type = "hyper"
            elif "event" in lower or "事件" in lower:
                extraction_type = "event"
            elif any(word in lower for word in ["triples", "triple", "三元组"]):
                extraction_type = "triples"

            return {
                "intent": "extract",
                "confidence": 0.78,
                "parameters": {
                    "extraction_type": extraction_type,
                    "data": "" if detected_path else text,
                    "file_path": detected_path,
                },
                "explanation": "detected extraction request",
            }

        if any(word in lower for word in ["help", "usage", "怎么用", "如何使用"]):
            return {
                "intent": "help",
                "confidence": 0.7,
                "explanation": "detected help request",
            }

        return None


def _explicit_reasoning_override(user_input: str) -> dict[str, Any] | None:
    text = user_input.strip()
    lower = text.lower()
    if not any(
        keyword in lower
        for keyword in [
            "complete kg",
            "complete the kg",
            "complete the last kg",
            "link prediction",
            "missing head",
            "missing tail",
            "completion",
        ]
    ):
        return None

    detected_path, _ = _extract_reasoning_paths(text)
    triplet_match = re.search(r"\(([^,]+),\s*([^,]+),\s*([^)]+)\)", text)
    query = None
    if triplet_match:
        query = {
            "source": triplet_match.group(1).strip(),
            "relation": triplet_match.group(2).strip(),
            "target": triplet_match.group(3).strip(),
        }

    use_last = ("last extraction" in lower or "last kg" in lower) and not detected_path
    return {
        "intent": "reason",
        "confidence": 0.9,
        "parameters": {
            "task_type": "completion",
            "method": "auto",
            "input_mode": "last_extraction" if use_last else "file" if detected_path else "auto",
            "input_path": detected_path,
            "data": None,
            "question": "",
            "query": query,
            "dataset": "",
            "metrics": [],
            "use_last_result": use_last,
            "use_last_file": False,
            "needs_confirmation": True,
        },
        "explanation": "detected explicit KG completion request",
    }


def _extract_reasoning_paths(text: str) -> tuple[str, str]:
    matches = re.findall(_PATH_PATTERN, text)
    if not matches:
        return "", ""
    if len(matches) == 1:
        return matches[0], ""

    question_like_index = -1
    for index, path in enumerate(matches):
        lower_path = path.lower()
        if "question" in lower_path or "questions" in lower_path:
            question_like_index = index
            break

    if question_like_index > 0:
        return matches[0], matches[question_like_index]

    if matches[0].lower().endswith(".jsonl") and not matches[1].lower().endswith(".jsonl"):
        return matches[1], matches[0]

    if matches[1].lower().endswith(".jsonl"):
        return matches[0], matches[1]

    return matches[0], ""
