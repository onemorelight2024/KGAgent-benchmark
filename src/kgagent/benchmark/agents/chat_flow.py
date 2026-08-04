"""Conversational state for benchmark generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kgagent.benchmark.tools.llm import resolve_api_key, resolve_base_url, resolve_model


@dataclass
class BenchmarkChatFlow:
    """Collect benchmark parameters across chat turns."""

    workspace: Path
    active: bool = False
    stage: str = "idle"
    graph_type: str | None = None
    task: str | None = None
    input_path: str | None = None
    sample_count: int | None = None
    method: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str = "gpt-5.4"
    run_id: str | None = None
    resume: bool = True
    batch_size: int | None = None
    language: str | None = None

    def __post_init__(self) -> None:
        """Seed API config from environment so chat does not ask unnecessarily."""
        self.base_url = self.base_url or resolve_base_url() or None
        self.api_key = self.api_key or resolve_api_key() or None
        self.model = resolve_model(self.model)

    def start(self, user_input: str = "") -> tuple[str, dict[str, Any] | None]:
        """Start or update the benchmark flow."""
        self.active = True
        self.stage = "graph_type"
        self._parse_any(user_input)
        return self.handle("")

    def handle(self, user_input: str) -> tuple[str, dict[str, Any] | None]:
        """Handle one user turn.

        Returns:
            A display message and optional run parameters.
        """
        if user_input:
            self._parse_any(user_input)

        missing = self._next_missing()
        if missing:
            self.stage = missing
            return self._question_for(missing), None

        params = {
            "data": self.input_path,
            "graph_type": self.graph_type,
            "task": self.task,
            "method": self.method or ("chronoqg" if self.graph_type == "TKG" else "sgsh_prompt"),
            "sample_count": self.sample_count or 5,
            "model": resolve_model(self.model),
            "base_url": self.base_url,
            "api_key": self.api_key,
            "run_id": self.run_id,
            "resume": self.resume,
            "batch_size": self.batch_size,
            "language": self.language,
        }
        self.active = False
        self.stage = "idle"
        return self._summary(), params

    def state(self) -> dict[str, Any]:
        """Return current structured state for benchmark intent parsing."""
        return {
            "graph_type": self.graph_type,
            "task": self.task,
            "input_path": self.input_path,
            "sample_count": self.sample_count,
            "method": self.method,
            "base_url": self.base_url,
            "api_key_set": bool(self.api_key),
            "model": self.model,
            "run_id": self.run_id,
            "resume": self.resume,
            "batch_size": self.batch_size,
            "language": self.language,
            "stage": self.stage,
        }

    def apply_update(self, update: dict[str, Any] | None) -> None:
        """Apply an LLM-parsed benchmark parameter update."""
        if not update:
            return
        for field in ("graph_type", "task", "input_path", "method", "base_url", "api_key", "model", "run_id"):
            value = update.get(field)
            if value:
                setattr(self, field, str(value))
        if update.get("language") in ("zh", "en"):
            self.language = str(update["language"])
        if update.get("sample_count") is not None:
            try:
                self.sample_count = int(update["sample_count"])
            except (TypeError, ValueError):
                pass
        if update.get("batch_size") is not None:
            try:
                self.batch_size = int(update["batch_size"])
            except (TypeError, ValueError):
                pass
        if update.get("resume") is not None:
            self.resume = bool(update["resume"])
        config_path = update.get("config_path")
        if config_path:
            self._load_api_config_from_file(str(config_path))

    def _parse_any(self, text: str) -> None:
        lowered = text.lower()

        if self.graph_type is None:
            if any(token in lowered for token in ("时序", "temporal", "tkg")) or text.strip() == "2":
                self.graph_type = "TKG"
            elif any(token in lowered for token in ("普通", "静态", "static", "kgqa", "kgqg", "kg ")) or text.strip() == "1":
                self.graph_type = "KG"

        if self.task is None:
            if "kgqg" in lowered or "问题生成" in text:
                self.task = "KGQG"
            elif "kgqa" in lowered or "问答" in text:
                self.task = "KGQA"

        path = self._extract_path(text)
        if path:
            if path.endswith(".md"):
                self._load_api_config_from_file(path)
            else:
                self.input_path = path

        count_match = re.search(r"(?<![\w-])(\d{1,5})\s*(?:个|条|samples?)", text, re.I)
        if count_match:
            self.sample_count = int(count_match.group(1))
        elif (
            self.sample_count is None
            and self.graph_type is not None
            and self.task is not None
            and self.input_path is not None
            and (sample_match := re.match(r"\s*(\d{1,5})(?:\s|[，,。]|$)", text))
        ):
            self.sample_count = int(sample_match.group(1))

        method = self._extract_method(text)
        if method:
            self.method = method

        url_match = re.search(r"https?://[^\s，,]+", text)
        if url_match:
            self.base_url = url_match.group(0).rstrip("。")

        key_match = re.search(r"sk-[A-Za-z0-9_\-]{4,}", text)
        if key_match:
            self.api_key = key_match.group(0)

        model_match = re.search(r"(?:model|模型)(?:用|为|是|=|:)?\s*([A-Za-z0-9_.\-]+)", text, re.I)
        if model_match:
            self.model = model_match.group(1)
        elif "5.4" in lowered:
            self.model = "gpt-5.4"

        run_id_match = re.search(r"(?:run[_ -]?id|运行id|任务id)(?:\s*(?:用|为|是|is|=|:))?\s*([A-Za-z0-9_.\-]+)", text, re.I)
        if run_id_match:
            self.run_id = run_id_match.group(1)

        batch_match = re.search(r"(?:batch(?:[_ -]?size)?|批量|每批)(?:用|为|是|=|:)?\s*(\d{1,5})", text, re.I)
        if batch_match:
            self.batch_size = int(batch_match.group(1))

        if any(token in lowered for token in ("不续跑", "不恢复", "重新跑", "no resume", "fresh")):
            self.resume = False
        elif any(token in lowered for token in ("断点", "续跑", "恢复", "resume")):
            self.resume = True

        if any(token in text for token in ("中文问题", "中文输出", "生成中文", "用中文生成")) or any(
            token in lowered for token in ("chinese output", "questions in chinese", "generate chinese")
        ):
            self.language = "zh"
        elif any(token in text for token in ("英文问题", "英文输出", "生成英文", "用英文生成")) or any(
            token in lowered for token in ("english output", "questions in english", "generate english")
        ):
            self.language = "en"

    def _extract_path(self, text: str) -> str | None:
        candidates = re.findall(
            r"(?:/[A-Za-z0-9_./~\- ]+\.(?:jsonl?|md)|[A-Za-z0-9_./~\-]+\.(?:jsonl?|md))",
            text,
        )
        for raw in candidates:
            candidate = raw.strip(" ，,。")
            path = Path(candidate)
            resolved = path if path.is_absolute() else self.workspace / path
            if resolved.exists() and resolved.is_file():
                return candidate
        return None

    def _load_api_config_from_file(self, raw_path: str) -> None:
        path = Path(raw_path)
        resolved = path if path.is_absolute() else self.workspace / path
        if not resolved.exists() or not resolved.is_file():
            return
        text = resolved.read_text(encoding="utf-8")
        url_match = re.search(r"https?://[^\s，,`\"']+", text)
        key_match = re.search(r"sk-[A-Za-z0-9_\-]{4,}", text)
        model_match = re.search(r"(?:默认|model|模型).*?(gpt-[A-Za-z0-9_.\-]+)", text, re.I)
        if url_match:
            self.base_url = url_match.group(0).rstrip("。")
        if key_match:
            self.api_key = key_match.group(0)
        if model_match:
            self.model = model_match.group(1)

    def _extract_method(self, text: str) -> str | None:
        lowered = text.lower()
        choice = text.strip().upper()
        if choice == "A":
            return "sgsh_prompt"
        if choice == "B":
            return "role_agent_qg"
        if choice == "D":
            return "sgsh_prompt"
        if choice == "C":
            return "chronoqg"
        if "roleagent" in lowered or "role agent" in lowered or any(token in text for token in ("角色", "多角色", "编审")):
            return "role_agent_qg"
        if "chronoqg" in lowered or "chrono qg" in lowered or "chrono_qg" in lowered or "时序方法" in text:
            return "chronoqg"
        if "sgsh" in lowered:
            return "sgsh_prompt"
        if any(token in lowered for token in ("骨架法", "骨架引导", "skeleton")) or "骨架" in text:
            return "sgsh_prompt"
        if any(token in lowered for token in ("选择最合适", "最合适", "default", "默认")):
            return "chronoqg" if self.graph_type == "TKG" else "sgsh_prompt"
        return None

    def _next_missing(self) -> str | None:
        if self.graph_type is None:
            return "graph_type"
        if self.task is None:
            return "task"
        if self.input_path is None:
            return "input_path"
        if self.sample_count is None:
            return "sample_count"
        if self.graph_type == "TKG" and self.method is None:
            self.method = "chronoqg"
        if self.method is None:
            return "method"
        return None

    def _question_for(self, stage: str, reply_language: str = "zh") -> str:
        if reply_language == "en":
            return self._question_for_en(stage)
        if stage == "graph_type":
            return (
                "benchmark 流程已准备好。目前支持两种类型：\n\n"
                "1. 普通 KGQA/KGQG：从普通知识图谱生成问答 benchmark。\n"
                "2. 时序 KGQA/KGQG：从带时间信息的时序知识图谱生成时序问答 benchmark。\n\n"
                "你需要生成哪种类型的 benchmark？"
            )
        if stage == "task":
            return "你要做 KGQA 还是 KGQG benchmark？"
        if stage == "input_path":
            return "请提供图谱 JSON 文件路径，例如 `examples/kg_benchmark_input.json`。"
        if stage == "sample_count":
            return "你希望生成多少个 benchmark 样本？"
        if stage == "method":
            return (
                "请选择生成方法：\n\n"
                "A. SGSH Prompt：免训练的骨架启发式提示方法。用 LLM prompt 替代可训练骨架生成器，"
                "再调用第二个 prompt 生成最终问题；每个样本 2 次 LLM 调用，简单、模块化、成本低。\n\n"
                "B. RoleAgentQG：多智能体编委方法（CIKM 2024）。由 Editor-in-Chief、Managing Editor、"
                "Contributor、Content Editor、Copy Editor 等角色按结构化协议协作，并通过审核-重写迭代控制质量；"
                "每个样本大约需要 6 次 LLM 调用，质量最高，成本也最高。\n\n"
                "你想用哪种方法？如果不确定，也可以说“选择最合适的方法”。"
            )
        if stage == "api":
            return (
                "Claude SDK 配置由当前终端环境提供。请确认已通过 CCR 激活，或直接说明 model。"
            )
        return "请继续提供 benchmark 配置。"

    def _question_for_en(self, stage: str) -> str:
        if stage == "graph_type":
            return (
                "The benchmark workflow is ready. Two graph types are supported:\n\n"
                "1. Ordinary KGQA/KGQG: generate QA benchmark data from a static knowledge graph.\n"
                "2. Temporal KGQA/KGQG: generate temporal QA benchmark data from a temporal knowledge graph.\n\n"
                "Which benchmark type do you want?"
            )
        if stage == "task":
            return "Do you want a KGQA or KGQG benchmark?"
        if stage == "input_path":
            return "Please provide the graph JSON file path, for example `examples/kg_benchmark_input.json`."
        if stage == "sample_count":
            return "How many benchmark samples do you want to generate?"
        if stage == "method":
            return (
                "Please choose a generation method:\n\n"
                "A. SGSH Prompt: a training-free skeleton heuristic prompting method. It replaces the trainable "
                "skeleton generator with an LLM skeleton prompt, then uses a second prompt to generate the final "
                "question; 2 LLM calls per sample, simple, modular, and low cost.\n\n"
                "B. RoleAgentQG: a multi-agent editorial-board method (CIKM 2024). Editor-in-Chief, Managing "
                "Editor, Contributor, Content Editor, and Copy Editor roles collaborate with review-rewrite "
                "iterations; about 6 LLM calls per sample, highest quality and highest cost.\n\n"
                "Which method do you want? If unsure, you can say \"choose the most suitable method\"."
            )
        if stage == "api":
            return (
                "Claude SDK config is provided by the current terminal environment. "
                "Please confirm CCR is activated, or specify a model directly."
            )
        return "Please continue providing the benchmark configuration."

    def _summary(self, reply_language: str = "zh") -> str:
        if reply_language == "en":
            return (
                "All required information is ready. Starting benchmark generation.\n\n"
                f"- Type: {self.task} / {self.graph_type}\n"
                f"- Graph: {self.input_path}\n"
                f"- Samples: {self.sample_count}\n"
                f"- Method: {self.method}\n"
                f"- Model: {self.model}\n"
                f"- run_id: {self.run_id or 'auto-generated'}\n"
                f"- Resume: {self.resume}"
            )
        return (
            "信息齐全，开始生成 benchmark。\n\n"
            f"- 类型：{self.task} / {self.graph_type}\n"
            f"- 图谱：{self.input_path}\n"
            f"- 样本数：{self.sample_count}\n"
            f"- 方法：{self.method}\n"
            f"- 模型：{self.model}\n"
            f"- run_id：{self.run_id or '自动生成'}\n"
            f"- 续跑：{self.resume}"
        )


def looks_like_benchmark_request(text: str) -> bool:
    """Return whether text should start the benchmark flow."""
    lowered = text.lower()
    return "benchmark" in lowered or "基准" in text or "评测数据" in text
