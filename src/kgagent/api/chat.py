"""Interactive chat interface for KGAgent."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from kgagent.system import KGAgentSystem
from kgagent.extraction.tools.loaders import load_json_file, save_json_file
from kgagent.output_process import record_result, format_for_display
from kgagent.output_process.record import format_single_result, clean_value, is_valid_triple_value
from kgagent.extraction.config import ExtractionConfig
from kgagent.intent import IntentEntry
from kgagent.core.session import ChatSession
from kgagent.core.batch import process_batch_with_resume
from kgagent.core.config import get_api_config, get_model
from kgagent.benchmark.agents.chat_flow import looks_like_benchmark_request
from kgagent.benchmark.agents.conversation import BenchmarkConversation

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings

    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


def _format_reasoning_result(task_type: str, result: dict[str, Any]) -> str:
    """Format reasoning output for terminal display."""
    result_task_type = str(result.get("task_type", task_type))
    if result_task_type == "qa_batch":
        summary = result.get("summary", {})
        lines = [
            f"Method: {result.get('method', 'unknown')}",
            f"Batch QA: total={summary.get('total', 0)}, success={summary.get('success', 0)}, failed={summary.get('failed', 0)}, skipped={summary.get('skipped', 0)}",
        ]
        resume_path = str(result.get("resume_path", "")).strip()
        if resume_path:
            lines.append(f"Resume file: {resume_path}")
        items = result.get("items", [])
        failed_items = [str(item.get("id", "")) for item in items if item.get("status") != "success"]
        if failed_items:
            lines.append("Failed items: " + ", ".join(failed_items[:10]))
            if len(failed_items) > 10:
                lines.append(f"... {len(failed_items) - 10} more failed item(s)")
        return "\n".join(lines)
    if task_type == "qa":
        method = result.get("method", "unknown")
        answer = result.get("answer", "")
        lines = [f"Method: {method}", f"Answer: {answer}"]
        kg_output_path = str(result.get("kg_output_path", "")).strip()
        if kg_output_path:
            lines.append(f"KG saved to: {kg_output_path}")
        return "\n".join(lines)
    return json.dumps(result, indent=2, ensure_ascii=False)


def _kg_input_from_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize extraction output for downstream reasoning."""
    if "relations" in result:
        return {"triples": result["relations"]}
    if "triples" in result:
        return {"triples": result["triples"]}
    if "relation_triples" in result:
        return {"triples": result["relation_triples"]}
    return result


def _resolve_reasoning_params(
    chat_session: ChatSession,
    params: dict[str, Any],
    *,
    raw_user_input: str = "",
) -> tuple[dict[str, Any], str, str]:
    """Resolve intent parameters into reasoning payload."""
    task_type = str(params.get("task_type", "qa"))
    payload: dict[str, Any] = {}
    input_type = "text"
    input_data = ""

    if task_type == "qa":
        payload["question"] = params.get("question", "")
        payload["method"] = params.get("method", "auto")
        questions_path = params.get("questions_path") or params.get("question_path")
        if isinstance(questions_path, str) and questions_path.strip():
            payload["questions_path"] = questions_path
        else:
            fallback_input_path, fallback_questions_path = _extract_reasoning_paths_from_text(raw_user_input)
            if fallback_questions_path:
                payload["questions_path"] = fallback_questions_path
                if not params.get("input_path") and fallback_input_path:
                    params["input_path"] = fallback_input_path
        if params.get("use_last_result"):
            last_record = chat_session.get_last_record()
            if last_record is None:
                raise ValueError("No previous result available for QA.")
            input_type = last_record.input_type
            input_data = last_record.input_data
            if last_record.operation_kind == "extraction":
                raise ValueError(
                    "QA over a previously extracted KG is no longer supported. "
                    "Please use the source document/corpus with rag_anything or graphrag."
                )
            else:
                reusable_path = last_record.metadata.get("input_path") or last_record.output_path
                if not reusable_path:
                    raise ValueError("The previous reasoning result does not expose a reusable input path.")
                payload["input_path"] = reusable_path
        elif params.get("use_last_file"):
            last_file = chat_session.get_last_file_path()
            if not last_file:
                raise ValueError("No previous file available in session history.")
            payload["input_path"] = last_file
            input_type = "file"
            input_data = last_file
        elif params.get("input_path"):
            payload["input_path"] = params["input_path"]
            input_type = "file"
            input_data = str(params["input_path"])
        elif params.get("data") is not None:
            raise ValueError(
                "QA currently requires a local input file or a reusable previous source file. "
                "Inline KG data is not supported in the chat entry."
            )
        else:
            raise ValueError("QA requires a file, previous result, or inline data.")

    elif task_type == "completion":
        payload["query"] = params.get("query") or {}
        method = params.get("method", "auto")
        if method != "auto":
            payload["method"] = method
        if params.get("input_path"):
            payload["kg_input"] = params["input_path"]
            input_type = "file"
            input_data = str(params["input_path"])
        elif params.get("use_last_result"):
            last_extraction = chat_session.get_last_extraction()
            if last_extraction is None:
                raise ValueError("No previous extraction result available for completion.")
            payload["kg"] = _kg_input_from_result(last_extraction.result)
            input_type = last_extraction.input_type
            input_data = last_extraction.input_data
        elif params.get("data") is not None:
            payload["kg"] = params["data"]
            input_data = str(params["data"])
        else:
            raise ValueError("Completion requires a KG file, previous extraction, or inline KG data.")

    else:
        raise ValueError(f"Unsupported reasoning task: {task_type}")

    return payload, input_type, input_data


def _extract_reasoning_paths_from_text(text: str) -> tuple[str, str]:
    pattern = r"([A-Za-z]:\\[^\s:]+(?:\.[A-Za-z0-9]+)?|[^\s]+(?:\.json|\.jsonl|\.txt|\.md|\.pdf|\.docx?|\.pptx?|\.xlsx?|\.png|\.jpg|\.jpeg))"
    matches = re.findall(pattern, text)
    if not matches:
        return "", ""
    if len(matches) == 1:
        return matches[0], ""

    question_index = -1
    for index, candidate in enumerate(matches):
        lower_candidate = candidate.lower()
        if "question" in lower_candidate or "questions" in lower_candidate:
            question_index = index
            break

    if question_index > 0:
        return matches[0], matches[question_index]

    if matches[1].lower().endswith(".jsonl"):
        return matches[0], matches[1]

    if matches[0].lower().endswith(".jsonl") and not matches[1].lower().endswith(".jsonl"):
        return matches[1], matches[0]

    return matches[0], ""


async def _maybe_prompt_save_kg_for_qa(
    payload: dict[str, Any],
    *,
    get_input: Any,
) -> None:
    save_reply = (await get_input("是否保存图谱到本地? [Y/N/Else]: ")).strip()
    if not save_reply:
        return

    lowered = save_reply.lower()
    if lowered in {"n", "no"}:
        payload["save_kg_json"] = False
        return

    if lowered in {"y", "yes"}:
        payload["save_kg_json"] = True
        return

    payload["save_kg_json"] = True
    payload["kg_output_path"] = save_reply


async def main_chat_async(workspace: str | None = None, model: str | None = None):
    """Run interactive chat interface.

    Args:
        workspace: Working directory (default: ./tmp_sdk)
        model: Model name (default: from config)
    """
    print("KGAgent - Knowledge Graph Extraction")
    print("=" * 50)
    print()
    print("Commands:")
    print("  Type your extraction request or file path")
    print("  :quit or :exit - Exit the chat")
    print("  :help - Show help")
    if PROMPT_TOOLKIT_AVAILABLE:
        print("  Ctrl+C - Cancel current task (stay in chat)")
        print("  Arrow keys - Navigate input history")
    print()
    print("Examples:")
    print("  extract triples: Alice works at Acme")
    print("  examples/data.json")
    print("  /path/to/file.json --type hyper")
    print("  /path/to/file.json --type event")
    print("  answer this question from examples/paper.pdf: what is the main conclusion?")
    print("  complete the last KG: (?, located_in, NYC)")
    print()

    get_api_config()
    chat_model = model or get_model()

    # Create system
    system = KGAgentSystem(
        model_name=chat_model,
        work_dir=workspace or "./tmp_sdk",
    )

    intent_config = ExtractionConfig(
        model_name=chat_model,
        work_dir=workspace or "./tmp_sdk",
        permission_mode="auto",
        max_turns=1,
    )
    intent_agent = IntentEntry(intent_config)

    chat_session = ChatSession(max_history=10)
    print(f"💾 Session memory enabled (keeping last 10 extractions)\n")
    benchmark_conversation = BenchmarkConversation(
        workspace=Path(workspace or "./tmp_sdk").resolve(),
        sdk_model=chat_model,
        benchmark_model="gpt-5.4",
    )

    if PROMPT_TOOLKIT_AVAILABLE:
        history = InMemoryHistory()
        bindings = KeyBindings()

        @bindings.add("c-c")
        def _(event):
            """Handle Ctrl+C - cancel current operation but stay in chat."""
            event.app.exit(exception=KeyboardInterrupt)

        prompt_session = PromptSession(history=history, key_bindings=bindings)

        async def get_input(prompt: str) -> str:
            """Get input with prompt_toolkit support (async)."""
            try:
                return await prompt_session.prompt_async(prompt)
            except KeyboardInterrupt:
                raise
            except EOFError:
                raise EOFError()
    else:
        async def get_input(prompt: str) -> str:
            """Fallback to basic input."""
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, input, prompt)

    current_task = None

    while True:
        try:
            user_input = (await get_input("User> ")).strip()
        except KeyboardInterrupt:
            if current_task and not current_task.done():
                print("\n⚠️  Cancelling current task...")
                current_task.cancel()
                try:
                    await current_task
                except asyncio.CancelledError:
                    print("✓ Task cancelled\n")
                current_task = None
                continue
            else:
                print("\n💡 Tip: Use :quit to exit\n")
                continue
        except EOFError:
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input in (":quit", ":exit"):
            print("Goodbye!")
            break

        if user_input == ":help":
            print("\nHelp:")
            print("  - Enter text for extraction")
            print("  - Enter file path to process file")
            print("  - File path can include --type <type>")
            print("  - Supported types: triples, temporal, hyper, event, auto")
            print("  - Multi-item files will be batch processed")
            print("  - Results from files are auto-saved to same directory")
            print("  - Ask QA over a file or a previous result")
            print("  - Ask for KG completion over the last KG or a KG file")
            print("  - Say 'save that' or 'save to <file>' to save the latest result")
            print("  - :stats - Show session statistics\n")
            continue

        if user_input == ":stats":
            stats = chat_session.get_session_stats()
            print("\n📊 Session Statistics:")
            print(f"  Duration: {stats['session_duration']:.0f} seconds")
            print(f"  Total extractions: {stats['total_extractions']}")
            print(f"  Total reasoning tasks: {stats['total_reasoning']}")
            print(f"  By extraction type: {stats['extraction_types']}")
            print(f"  By reasoning type: {stats['reasoning_types']}")
            print(f"  Files processed: {stats['files_processed']}")
            print(f"  Text extractions: {stats['text_extractions']}\n")
            continue

        # Benchmark is a multi-turn agent workflow. The benchmark conversation
        # agent interprets natural replies and updates the workflow state.
        if benchmark_conversation.active or looks_like_benchmark_request(user_input):
            try:
                if benchmark_conversation.active:
                    message, benchmark_params = await benchmark_conversation.handle(user_input)
                else:
                    message, benchmark_params = await benchmark_conversation.start(user_input)
            except Exception as e:
                print(f"\nError: benchmark 对话失败: {e}\n")
                continue
            print(f"\nAssistant> {message}\n")

            if benchmark_params is None:
                continue

            try:
                current_task = asyncio.create_task(
                    system.benchmark_async(**benchmark_params)
                )
                result = await current_task
                current_task = None
                stats = result.get("stats", {})
                output_path = result.get("output_path")
                _print_benchmark_result(result, output_path, benchmark_conversation.current_reply_language())

                chat_session.add_extraction(
                    input_type="benchmark",
                    input_data=benchmark_params["data"],
                    extraction_type=result.get("benchmark_type", "benchmark"),
                    result=result,
                    output_path=output_path,
                )
            except asyncio.CancelledError:
                print("\n⚠️  benchmark 生成已取消\n")
                current_task = None
            except Exception as e:
                print(f"\nError: benchmark 生成失败: {e}\n")
                current_task = None
            continue

        # === Step 1: Parse intent with Intent Agent ===
        try:
            context = chat_session.build_context_summary(max_records=3)
            intent_result = await intent_agent.parse_intent_async(user_input, context=context)
        except Exception as e:
            print(f"⚠️  Intent 解析失败: {e}")
            print("回退到传统模式...\n")
            intent_result = None

        if intent_result and intent_result.get("intent") == "chat":
            print(f"\nAssistant> {intent_result.get('response', '你好！')}\n")
            continue

        if intent_result and intent_result.get("intent") == "help":
            print("\nHelp:")
            print("  - 直接输入文本让我抽取知识图谱")
            print("  - 或者输入文件路径，例如: examples/data.json")
            print("  - 支持的抽取类型: triples(三元组), temporal(时序), hyper(超关系), event(事件)")
            print("  - 使用 --type 指定类型，例如: data.json --type event")
            print("  - 生成 benchmark: 例如 '我想做 KGQA benchmark' 或 kgagent benchmark examples/kg_benchmark_input.json")
            print("  - 也支持 QA、补全这两类 reasoning 任务")
            print("  - 输入 :quit 退出\n")
            continue

        if intent_result and intent_result.get("intent") == "command":
            command = intent_result.get("command", "")
            if command in ("quit", "exit"):
                print("Goodbye!")
                break
            elif command == "help":
                continue

        if intent_result and intent_result.get("intent") == "save":
            params = intent_result.get("parameters", {})
            file_path = params.get("file_path")
            last_record = chat_session.get_last_record()
            if last_record is None:
                print("⚠️  没有可保存的结果\n")
                continue

            if not file_path:
                import time

                timestamp = time.strftime("%Y%m%d_%H%M%S")
                file_path = f"kg_result_{timestamp}.json"

            try:
                if last_record.operation_kind == "extraction":
                    formatted = format_single_result(
                        last_record.result,
                        input_data=last_record.input_data,
                        extraction_type=last_record.task_name or last_record.extraction_type,
                    )
                    save_json_file(formatted, file_path)
                else:
                    save_json_file(last_record.result, file_path)
                print(f"✓ 结果已保存到: {file_path}\n")
            except Exception as e:
                print(f"⚠️  保存失败: {e}\n")

            continue

        if intent_result and intent_result.get("intent") == "convert":
            params = intent_result.get("parameters", {})
            source = params.get("source", "last")
            target_format = params.get("target_format", "neo4j_csv")
            output_path = params.get("output_path")

            try:
                last_result = None
                if source == "last":
                    last_record = chat_session.get_last_extraction()
                    if last_record is None:
                        print("鈿狅笍  娌℃湁鍙浆鎹㈢殑鎶藉彇缁撴灉\n")
                        continue
                    last_result = format_single_result(
                        last_record.result,
                        input_data=last_record.input_data,
                        extraction_type=last_record.extraction_type,
                    )

                from kgagent.system.orchestrator import run_conversion

                result = await run_conversion(
                    source=source,
                    target_format=target_format,
                    output_path=output_path,
                    last_result=last_result,
                )

                output_target = result.get("output_dir") or result.get("output_file") or ""
                print("\nAssistant>")
                print("Conversion complete")
                if output_target:
                    print(f"Output: {output_target}")
                print()
            except Exception as e:
                print(f"\nError: {e}\n")
            continue

        if intent_result and intent_result.get("intent") == "import":
            params = intent_result.get("parameters", {})
            input_path = params.get("input_path")
            output_path = params.get("output_path")

            if not input_path:
                print("鈿狅笍  鏈寚瀹氳緭鍏ユ枃浠惰矾寰刓n")
                continue

            try:
                result = system.convert_from(
                    input_path=input_path,
                    output_path=output_path,
                )
                print("\nAssistant>")
                print("Import complete")
                print(f"Output: {result.get('output_file', '')}\n")
            except Exception as e:
                print(f"\nError: {e}\n")
            continue

        if intent_result and intent_result.get("intent") == "parse":
            params = intent_result.get("parameters", {})
            input_path = params.get("input_path")
            output_path = params.get("output_path")

            if not input_path:
                print("鈿狅笍  鏈寚瀹氳緭鍏ユ枃浠惰矾寰刓n")
                continue

            try:
                result = await system.parse_document_async(
                    input_path=input_path,
                    output_path=output_path,
                )
                if result.get("success"):
                    print("\nAssistant>")
                    print("Parse complete")
                    print(f"Output: {result.get('output_file', '')}\n")
                else:
                    print(f"\nError: {result.get('error', 'parse failed')}\n")
            except Exception as e:
                print(f"\nError: {e}\n")
            continue

        if intent_result and intent_result.get("intent") == "reason":
            params = intent_result.get("parameters", {})
            task_type = str(params.get("task_type", "qa"))

            print(f"\n✓ 意图: {intent_result.get('explanation', 'reasoning')}")
            print(f"✓ 推理类型: {task_type}")

            if params.get("input_path"):
                print(f"✓ 文件: {params['input_path']}")
            elif params.get("dataset"):
                print(f"✓ 数据集: {params['dataset']}")
            elif params.get("use_last_result"):
                print("✓ 输入来源: 上一次结果")
            elif params.get("use_last_file"):
                print("✓ 输入来源: 上一个文件")

            if params.get("needs_confirmation"):
                confirm = (await get_input("\n确认执行? [Y/n]: ")).strip().lower()
                if confirm == "n":
                    print("已取消\n")
                    continue

            try:
                payload, input_type, input_data = _resolve_reasoning_params(
                    chat_session,
                    params,
                    raw_user_input=user_input,
                )
                if task_type == "qa":
                    await _maybe_prompt_save_kg_for_qa(payload, get_input=get_input)
                current_task = asyncio.create_task(
                    system.reason_async(
                        data=payload,
                        task_type=task_type,
                    )
                )

                result = await current_task
                current_task = None

                print(f"\nAssistant>\n{_format_reasoning_result(task_type, result)}\n")

                chat_session.add_reasoning(
                    input_type=input_type,
                    input_data=input_data or user_input,
                    task_type=task_type,
                    result=result,
                    output_path=result.get("_saved_to"),
                    input_path=payload.get("input_path") or payload.get("dataset"),
                    method=payload.get("method", "auto"),
                )
            except asyncio.CancelledError:
                print("\n⚠️  Reasoning cancelled\n")
                current_task = None
            except Exception as e:
                print(f"\nError: {e}\n")
                current_task = None
            continue

        if not intent_result or intent_result.get("intent") not in (
            "extract",
            "reason",
            "chat",
            "help",
            "command",
            "save",
            "convert",
            "import",
            "parse",
        ):
            print("⚠️  无法确定意图，使用传统解析模式\n")

        if intent_result and intent_result.get("intent") == "extract":
            params = intent_result.get("parameters", {})

            print(f"\n✓ 意图: {intent_result.get('explanation', '知识图谱抽取')}")
            print(f"✓ 抽取类型: {params.get('extraction_type', 'auto')}")

            if params.get("file_path"):
                print(f"✓ 文件: {params['file_path']}")
            else:
                data_preview = params.get("data", "")[:80]
                if len(params.get("data", "")) > 80:
                    data_preview += "..."
                print(f"✓ 数据: {data_preview}")

            confirm = (await get_input("\n确认执行? [Y/n/edit]: ")).strip().lower()

            if confirm == "n":
                print("已取消\n")
                continue
            elif confirm == "edit":
                print("\n请选择抽取类型：")
                print("  1. triples - 关系三元组")
                print("  2. temporal - 时序四元组")
                print("  3. hyper - 超关系")
                print("  4. event - 事件图谱")
                choice = (await get_input("请输入选项 (1/2/3/4): ")).strip()
                type_map = {"1": "triples", "2": "temporal", "3": "hyper", "4": "event"}
                extraction_type = type_map.get(choice, params.get("extraction_type", "auto"))
            else:
                extraction_type = params.get("extraction_type", "auto")

            if params.get("file_path"):
                user_input = params["file_path"]
                if extraction_type != "auto":
                    user_input += f" --type {extraction_type}"
            else:
                data = params.get("data", user_input)
                user_input = data

        if not intent_result or intent_result.get("intent") == "extract":
            if not intent_result or intent_result.get("intent") != "extract":
                extraction_type = "auto"
                data = user_input
                is_file_input = False

                if "--type" in user_input:
                    parts = user_input.split("--type")
                    data = parts[0].strip()
                    type_part = parts[1].strip().split()[0]
                    extraction_type = type_part
                else:
                    from kgagent.system.registry import ExtractionRegistry

                    registry = ExtractionRegistry()
                    detected_type = registry.detect_type(user_input)

                    has_keyword = False
                    user_input_lower = user_input.lower()
                    for type_info in registry.types.values():
                        for keyword in type_info.keywords:
                            if keyword in user_input_lower:
                                has_keyword = True
                                extraction_type = detected_type
                                break
                        if has_keyword:
                            break
            else:
                data = user_input
                is_file_input = False
        else:
            continue

        potential_path = None
        words = data.split()
        for word in words:
            word_path = Path(word)
            if word_path.exists() and word_path.is_file():
                potential_path = word_path
                break

        file_path = potential_path or Path(data)
        if file_path.exists() and file_path.is_file():
            print(f"\n📁 Detected file: {file_path}")
            try:
                loaded_data = load_json_file(str(file_path))

                if isinstance(loaded_data, list):
                    print(f"🔄 Batch processing {len(loaded_data)} items...\n")

                    stem = file_path.stem
                    output_path = file_path.parent / f"{stem}_{extraction_type}_kg.json"

                    def format_result(result, item, index):
                        formatted = {"index": index}

                        if isinstance(item, dict):
                            text = (
                                item.get("text")
                                or item.get("content")
                                or item.get("description")
                                or item.get("body")
                                or str(item)
                            )
                            formatted["text"] = text
                        elif isinstance(item, str):
                            formatted["text"] = item

                        kg = []
                        if "error" in result:
                            formatted["error"] = result["error"]
                        else:
                            if "relations" in result:
                                for rel in result["relations"]:
                                    try:
                                        if isinstance(rel, (list, tuple)) and len(rel) == 3:
                                            s, r, o = rel
                                            if not (
                                                is_valid_triple_value(s)
                                                and is_valid_triple_value(r)
                                                and is_valid_triple_value(o)
                                            ):
                                                continue
                                            s = clean_value(s)
                                            r = clean_value(r)
                                            o = clean_value(o)
                                            kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                                    except (ValueError, TypeError):
                                        continue
                            elif "triples" in result:
                                for rel in result["triples"]:
                                    try:
                                        if isinstance(rel, (list, tuple)) and len(rel) == 3:
                                            s, r, o = rel
                                            if not (
                                                is_valid_triple_value(s)
                                                and is_valid_triple_value(r)
                                                and is_valid_triple_value(o)
                                            ):
                                                continue
                                            s = clean_value(s)
                                            r = clean_value(r)
                                            o = clean_value(o)
                                            kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                                    except (ValueError, TypeError):
                                        continue
                            elif "relation_triples" in result:
                                for rel in result["relation_triples"]:
                                    try:
                                        if isinstance(rel, (list, tuple)) and len(rel) == 3:
                                            s, r, o = rel
                                            if not (
                                                is_valid_triple_value(s)
                                                and is_valid_triple_value(r)
                                                and is_valid_triple_value(o)
                                            ):
                                                continue
                                            s = clean_value(s)
                                            r = clean_value(r)
                                            o = clean_value(o)
                                            kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                                    except (ValueError, TypeError):
                                        continue
                            elif "quadruples" in result:
                                kg = result["quadruples"]
                            elif "hyper_relations" in result:
                                kg = result["hyper_relations"]
                            elif "entity_relation_dict" in result or "event_entity_relation_dict" in result:
                                kg = {
                                    "entity_relations": result.get("entity_relation_dict", []),
                                    "event_entities": result.get("event_entity_relation_dict", []),
                                    "event_relations": result.get("event_relation_dict", []),
                                }

                        formatted["kg"] = kg
                        return formatted

                    async def extract_single(item):
                        return await system.extract_async(
                            data=item,
                            extraction_type=extraction_type,
                        )

                    current_task = asyncio.create_task(
                        process_batch_with_resume(
                            items=loaded_data,
                            process_fn=extract_single,
                            output_path=output_path,
                            max_concurrent=4,
                            format_fn=format_result,
                        )
                    )

                    try:
                        stats = await current_task
                    except asyncio.CancelledError:
                        print("\n⚠️  Batch processing cancelled\n")
                        current_task = None
                        continue
                    finally:
                        current_task = None

                    print(f"\n✓ Batch processing complete:")
                    print(f"  Total: {stats['total']}")
                    print(f"  Completed: {stats['completed']}")
                    print(f"  Skipped: {stats['skipped']}")
                    print(f"  Failed: {stats['failed']}")
                    print(f"📝 Results saved to: {stats['output_path']}\n")
                    continue

                elif isinstance(loaded_data, dict) and "tasks" in loaded_data:
                    tasks_list = loaded_data["tasks"]
                    print(f"🔄 Batch processing {len(tasks_list)} tasks from config...\n")

                    stem = file_path.stem
                    output_path = file_path.parent / f"{stem}_{extraction_type}_kg.json"

                    items_with_types = []
                    for task in tasks_list:
                        task_type = task.get("extraction_type", extraction_type)
                        task_data = task.get("data", task)
                        items_with_types.append((task_data, task_type))

                    def format_result(result, item_tuple, index):
                        item, item_type = item_tuple
                        formatted = {"index": index}

                        if isinstance(item, dict):
                            text = (
                                item.get("text")
                                or item.get("content")
                                or item.get("description")
                                or item.get("body")
                                or str(item)
                            )
                            formatted["text"] = text
                        elif isinstance(item, str):
                            formatted["text"] = item

                        kg = []
                        if "error" in result:
                            formatted["error"] = result["error"]
                        else:
                            if "relations" in result:
                                for rel in result["relations"]:
                                    try:
                                        if isinstance(rel, (list, tuple)) and len(rel) == 3:
                                            s, r, o = rel
                                            kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                                    except (ValueError, TypeError):
                                        continue
                            elif "triples" in result:
                                for rel in result["triples"]:
                                    try:
                                        if isinstance(rel, (list, tuple)) and len(rel) == 3:
                                            s, r, o = rel
                                            kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                                    except (ValueError, TypeError):
                                        continue
                            elif "relation_triples" in result:
                                for rel in result["relation_triples"]:
                                    try:
                                        if isinstance(rel, (list, tuple)) and len(rel) == 3:
                                            s, r, o = rel
                                            kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                                    except (ValueError, TypeError):
                                        continue
                            elif "quadruples" in result:
                                kg = result["quadruples"]
                            elif "hyper_relations" in result:
                                kg = result["hyper_relations"]
                            elif "entity_relation_dict" in result or "event_entity_relation_dict" in result:
                                kg = {
                                    "entity_relations": result.get("entity_relation_dict", []),
                                    "event_entities": result.get("event_entity_relation_dict", []),
                                    "event_relations": result.get("event_relation_dict", []),
                                }

                        formatted["kg"] = kg
                        return formatted

                    async def extract_single(item_tuple):
                        item, item_type = item_tuple
                        return await system.extract_async(
                            data=item,
                            extraction_type=item_type,
                        )

                    current_task = asyncio.create_task(
                        process_batch_with_resume(
                            items=items_with_types,
                            process_fn=extract_single,
                            output_path=output_path,
                            max_concurrent=4,
                            format_fn=format_result,
                        )
                    )

                    try:
                        stats = await current_task
                    except asyncio.CancelledError:
                        print("\n⚠️  Batch processing cancelled\n")
                        current_task = None
                        continue
                    finally:
                        current_task = None

                    print(f"\n✓ Batch processing complete:")
                    print(f"  Total: {stats['total']}")
                    print(f"  Completed: {stats['completed']}")
                    print(f"  Skipped: {stats['skipped']}")
                    print(f"  Failed: {stats['failed']}")
                    print(f"📝 Results saved to: {stats['output_path']}\n")
                    continue

                else:
                    print("📝 Processing single item...\n")
                    data = loaded_data
                    is_file_input = True

            except Exception as e:
                print(f"\nError loading file: {e}\n")
                continue
        else:
            is_file_input = False

        try:
            if extraction_type == "auto":
                from kgagent.system.registry import ExtractionRegistry

                registry = ExtractionRegistry()

                detect_text = data if isinstance(data, str) else ""
                detected_type = registry.detect_type(detect_text)

                has_keyword = False
                if detect_text:
                    detect_text_lower = detect_text.lower()
                    for type_info in registry.types.values():
                        for keyword in type_info.keywords:
                            if keyword in detect_text_lower:
                                has_keyword = True
                                break
                        if has_keyword:
                            break

                if has_keyword:
                    extraction_type = detected_type
                else:
                    print("\n⚠️  无法自动检测抽取类型，请选择：")
                    print("  1. triples - 关系三元组 (subject, relation, object)")
                    print("  2. temporal - 时序四元组 (subject, relation, object, time)")
                    print("  3. hyper - 超关系 (relation with attributes)")
                    print("  4. event - 事件图谱 (AutoSchemaKG 三阶段)")
                    print()

                    choice = (await get_input("请输入选项 (1/2/3/4，直接回车默认为 triples): ")).strip()

                    type_map = {
                        "1": "triples",
                        "2": "temporal",
                        "3": "hyper",
                        "4": "event",
                        "": "triples",
                    }

                    extraction_type = type_map.get(choice, "triples")
                    print(f"✓ 已选择: {extraction_type}\n")

            current_task = asyncio.create_task(
                system.extract_async(
                    data=data,
                    extraction_type=extraction_type,
                )
            )

            result = await current_task
            current_task = None

            if is_file_input:
                output_path = record_result(result, file_path)
                print("✓ Extraction complete")
                print(f"📝 Result saved to: {output_path}\n")

                chat_session.add_extraction(
                    input_type="file",
                    input_data=str(file_path),
                    extraction_type=extraction_type,
                    result=result,
                    output_path=output_path,
                )
            else:
                result_str = format_for_display(result, extraction_type)
                print(f"\nAssistant>\n{result_str}\n")

                chat_session.add_extraction(
                    input_type="text",
                    input_data=data,
                    extraction_type=extraction_type,
                    result=result,
                    output_path=None,
                )

        except asyncio.CancelledError:
            print("\n⚠️  Extraction cancelled\n")
            current_task = None
        except Exception as e:
            print(f"\nError: {e}\n")
            current_task = None


def main_chat(workspace: str | None = None, model: str | None = None):
    """Synchronous wrapper for chat."""
    asyncio.run(main_chat_async(workspace, model))


def _print_benchmark_result(result: dict, output_path: str | None, reply_language: str) -> None:
    """Print benchmark completion summary in the active conversation language."""
    stats = result.get("stats", {})
    if reply_language == "en":
        print("\nAssistant> benchmark generation completed.\n")
        print("| Item | Result |")
        print("|---|---|")
        print(f"| Type | {result.get('benchmark_type')} / {result.get('graph_type')} |")
        print(f"| Method | {result.get('method')} |")
        print(f"| Model | {result.get('model')} |")
        print(f"| Samples | {stats.get('valid', 0)} / {stats.get('total', 0)} valid |")
        print(f"| Output file | `{output_path}` |")
        print()
        return

    print("\nAssistant> benchmark 生成完成。\n")
    print("| 项目 | 结果 |")
    print("|---|---|")
    print(f"| 类型 | {result.get('benchmark_type')} / {result.get('graph_type')} |")
    print(f"| 方法 | {result.get('method')} |")
    print(f"| 模型 | {result.get('model')} |")
    print(f"| 样本数 | {stats.get('valid', 0)} / {stats.get('total', 0)} 有效 |")
    print(f"| 输出文件 | `{output_path}` |")
    print()
