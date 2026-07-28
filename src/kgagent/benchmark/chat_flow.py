"""Conversational state for benchmark generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kgagent.benchmark.llm import mask_secret, resolve_api_key, resolve_base_url


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
    model: str = "gpt-4o-mini"

    def __post_init__(self) -> None:
        """Seed API config from environment so chat does not ask unnecessarily."""
        self.base_url = self.base_url or resolve_base_url() or None
        self.api_key = self.api_key or resolve_api_key() or None

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
            "method": self.method or "sgsh_prompt",
            "sample_count": self.sample_count or 5,
            "model": self.model or "gpt-4o-mini",
            "base_url": self.base_url,
            "api_key": self.api_key,
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
            "stage": self.stage,
        }

    def apply_update(self, update: dict[str, Any] | None) -> None:
        """Apply an LLM-parsed benchmark parameter update."""
        if not update:
            return
        for field in ("graph_type", "task", "input_path", "method", "base_url", "api_key", "model"):
            value = update.get(field)
            if value:
                setattr(self, field, str(value))
        if update.get("sample_count") is not None:
            try:
                self.sample_count = int(update["sample_count"])
            except (TypeError, ValueError):
                pass
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
            and re.fullmatch(r"\d{1,5}", text.strip())
        ):
            self.sample_count = int(text.strip())

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
        elif "4o-mini" in lowered:
            self.model = "gpt-4o-mini"
        elif "gpt-4.1-mini" in lowered:
            self.model = "gpt-4.1-mini"

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
            return "role_agent_qg"
        if choice == "B":
            return "kqg_cot_plus"
        if choice == "C":
            return "r2dqg_prompt"
        if choice == "D":
            return "sgsh_prompt"
        if choice == "E":
            return "chronoqg"
        if "chronoqg" in lowered or "chronoqg" in lowered or "chrono qg" in lowered:
            return "chronoqg"
        if text.strip().upper() == "D" or "sgsh" in lowered:
            return "sgsh_prompt"
        if any(token in lowered for token in ("骨架法", "骨架引导", "skeleton")) or "骨架" in text:
            return "sgsh_prompt"
        if any(token in lowered for token in ("选择最合适", "最合适", "default", "默认")):
            if self.graph_type == "TKG":
                return "chronoqg"
            return "sgsh_prompt"
        if "roleagent" in lowered or "role agent" in lowered or any(token in text for token in ("角色", "多角色", "编审")):
            return "role_agent_qg"
        if "r2dqg" in lowered or any(token in text for token in ("草稿", "精炼")):
            return "r2dqg_prompt"
        if "kqg" in lowered or "cot" in lowered or any(token in text for token in ("思维链", "推理")):
            return "kqg_cot_plus"
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
        if self.method is None:
            return "method"
        if self.base_url is None or self.api_key is None:
            return "api"
        return None

    def _question_for(self, stage: str) -> str:
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
            if self.graph_type == "TKG":
                return (
                    "请选择生成方法：\n\n"
                    "D. SGSH Prompt：轻量时序问答生成，适合快速验证。\n"
                    "E. ChronoQG：时序约束采样、改写和验证流程，适合正式 temporal benchmark。\n\n"
                    "如果没有特殊要求，可以回复“选择最合适的方法”。"
                )
            return (
                "请选择生成方法：\n\n"
                "A. RoleAgentQG：多智能体编委方法（CIKM 2024）。由 Editor-in-Chief、Managing Editor、"
                "Contributor、Content Editor、Copy Editor 等角色按结构化协议协作，并通过审核-重写迭代控制质量；"
                "每个样本大约需要 6 次 LLM 调用，质量最高，成本也最高。\n\n"
                "B. KQG-CoT+：思维链提示方法（EMNLP 2023）。按子图相似度选择 few-shot 示例，先生成中间子问题，"
                "再综合成最终问题；质量稳定，成本适中，适合推理型问题。\n\n"
                "C. R2DQG：骨架引导的 draft-and-refine 方法（IJCAI 2025）。先生成多样化问题骨架，填入实体生成候选问题，"
                "再进行自我纠错和精炼；多样性最高，成本和稳定性适中。\n\n"
                "D. SGSH Prompt：免训练的骨架启发式提示方法。用 LLM prompt 替代可训练骨架生成器，"
                "再调用第二个 prompt 生成最终问题；每个样本 2 次 LLM 调用，简单、模块化、成本低。\n\n"
                "你想用哪种方法？如果不确定，也可以说“选择最合适的方法”。"
            )
        if stage == "api":
            return (
                "请提供 OpenAI-compatible API 配置：`base_url` 和 `api_key`。"
                "模型默认使用 `gpt-4o-mini`，也可以直接说明 model。"
            )
        return "请继续提供 benchmark 配置。"

    def _summary(self) -> str:
        return (
            "信息齐全，开始生成 benchmark。\n\n"
            f"- 类型：{self.task} / {self.graph_type}\n"
            f"- 图谱：{self.input_path}\n"
            f"- 样本数：{self.sample_count}\n"
            f"- 方法：{self.method}\n"
            f"- 模型：{self.model}\n"
            f"- API：{self.base_url}\n"
            f"- Key：{mask_secret(self.api_key)}"
        )


def looks_like_benchmark_request(text: str) -> bool:
    """Return whether text should start the benchmark flow."""
    lowered = text.lower()
    return "benchmark" in lowered or "基准" in text or "评测数据" in text
