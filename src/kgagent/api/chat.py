"""Interactive chat interface for KGAgent."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kgagent.system import KGAgentSystem
from kgagent.extraction.tools.loaders import load_json_file, save_json_file
from kgagent.output_process import record_result, record_batch_results, format_for_display
from kgagent.output_process.record import format_single_result, clean_value
from kgagent.extraction.config import ExtractionConfig
from kgagent.intent import IntentEntry
from kgagent.core.session import ChatSession
from kgagent.core.batch import process_batch_with_resume

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


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
    print()

    # Create system
    system = KGAgentSystem(
        model_name=model,
        work_dir=workspace or "./tmp_sdk",
    )

    # Create intent agent
    intent_config = ExtractionConfig(
        model_name=model,
        work_dir=workspace or "./tmp_sdk",
        permission_mode="auto",
        max_turns=1,
    )
    intent_agent = IntentEntry(intent_config)

    # Create chat session for memory
    chat_session = ChatSession(max_history=10)
    print(f"💾 Session memory enabled (keeping last 10 extractions)\n")

    # Setup prompt session if available
    if PROMPT_TOOLKIT_AVAILABLE:
        history = InMemoryHistory()
        bindings = KeyBindings()

        @bindings.add('c-c')
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
            print("  - Say 'save that' or 'save to <file>' to save text extraction results")
            print("  - :stats - Show session statistics\n")
            continue

        if user_input == ":stats":
            stats = chat_session.get_session_stats()
            print("\n📊 Session Statistics:")
            print(f"  Duration: {stats['session_duration']:.0f} seconds")
            print(f"  Total extractions: {stats['total_extractions']}")
            print(f"  By type: {stats['extraction_types']}")
            print(f"  Files processed: {stats['files_processed']}")
            print(f"  Text extractions: {stats['text_extractions']}\n")
            continue

        # === Step 1: Parse intent with Intent Agent ===
        try:
            # Build context from session history
            context = chat_session.build_context_summary(max_records=3)
            intent_result = await intent_agent.parse_intent_async(user_input, context=context)
        except Exception as e:
            print(f"⚠️  Intent 解析失败: {e}")
            print("回退到传统模式...\n")
            intent_result = None

        # === Step 2: Handle different intents ===

        # Handle chat intent
        if intent_result and intent_result.get("intent") == "chat":
            print(f"\nAssistant> {intent_result.get('response', '你好！')}\n")
            continue

        # Handle help intent
        if intent_result and intent_result.get("intent") == "help":
            print("\nHelp:")
            print("  - 直接输入文本让我抽取知识图谱")
            print("  - 或者输入文件路径，例如: examples/data.json")
            print("  - 支持的抽取类型: triples(三元组), temporal(时序), hyper(超关系), event(事件)")
            print("  - 使用 --type 指定类型，例如: data.json --type event")
            print("  - 输入 :quit 退出\n")
            continue

        # Handle command intent (already handled above, but for completeness)
        if intent_result and intent_result.get("intent") == "command":
            command = intent_result.get("command", "")
            if command in ("quit", "exit"):
                print("Goodbye!")
                break
            elif command == "help":
                continue  # Already handled above

        # Handle save intent
        if intent_result and intent_result.get("intent") == "save":
            params = intent_result.get("parameters", {})
            target = params.get("target", "last")
            file_path = params.get("file_path")

            if target == "last":
                last_record = chat_session.get_last_extraction()
                if last_record is None:
                    print("⚠️  没有可保存的抽取结果\n")
                    continue

                # Generate file path if not provided
                if not file_path:
                    import time
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    file_path = f"kg_result_{timestamp}.json"

                # Format and save the result using record_result
                # This ensures consistent tagged format
                try:
                    # Create a temporary file path for record_result
                    temp_path = Path(file_path)

                    # Use record_result to format and save
                    # It expects input_data, so we provide the original input
                    from kgagent.output_process.record import format_single_result

                    # Format result with proper tagged format
                    formatted = format_single_result(
                        last_record.result,
                        input_data=last_record.input_data,
                        extraction_type=last_record.extraction_type,
                    )

                    # Save formatted result
                    save_json_file(formatted, file_path)
                    print(f"✓ 结果已保存到: {file_path}\n")
                except Exception as e:
                    print(f"⚠️  保存失败: {e}\n")

            continue

        # === Step 3: Handle extract intent ===

        # If intent agent identified extract intent, show confirmation
        if intent_result and intent_result.get("intent") == "extract":
            params = intent_result.get("parameters", {})

            print(f"\n✓ 意图: {intent_result.get('explanation', '知识图谱抽取')}")
            print(f"✓ 抽取类型: {params.get('extraction_type', 'auto')}")

            if params.get("file_path"):
                print(f"✓ 文件: {params['file_path']}")
            else:
                data_preview = params.get('data', '')[:80]
                if len(params.get('data', '')) > 80:
                    data_preview += "..."
                print(f"✓ 数据: {data_preview}")

            # Ask for confirmation
            confirm = (await get_input("\n确认执行? [Y/n/edit]: ")).strip().lower()

            if confirm == 'n':
                print("已取消\n")
                continue
            elif confirm == 'edit':
                # Let user manually choose type
                print("\n请选择抽取类型：")
                print("  1. triples - 关系三元组")
                print("  2. temporal - 时序四元组")
                print("  3. hyper - 超关系")
                print("  4. event - 事件图谱")
                choice = (await get_input("请输入选项 (1/2/3/4): ")).strip()
                type_map = {"1": "triples", "2": "temporal", "3": "hyper", "4": "event"}
                extraction_type = type_map.get(choice, params.get('extraction_type', 'auto'))
            else:
                # User confirmed, use detected type
                extraction_type = params.get('extraction_type', 'auto')

            # Prepare data
            if params.get("file_path"):
                user_input = params['file_path']
                if extraction_type != 'auto':
                    user_input += f" --type {extraction_type}"
            else:
                data = params.get('data', user_input)
                # Continue with normal extraction flow below
                user_input = data

        # If intent agent failed or returned None, fall back to original logic
        if not intent_result or intent_result.get("intent") not in ("extract", "chat", "help", "command"):
            print("⚠️  无法确定意图，使用传统解析模式\n")

        # === Original extraction logic (for fallback or after intent confirmation) ===

        # Parse input for extraction type and file path
        if not intent_result or intent_result.get("intent") == "extract":
            # If we came from intent agent with extract intent, extraction_type is already set
            # Otherwise, parse it from user_input
            if not intent_result or intent_result.get("intent") != "extract":
                extraction_type = "auto"
                data = user_input
                is_file_input = False

                # Check if input contains --type flag
                if "--type" in user_input:
                    parts = user_input.split("--type")
                    data = parts[0].strip()
                    type_part = parts[1].strip().split()[0]
                    extraction_type = type_part
                else:
                    # Try to detect extraction type from user input (before file loading)
                    from kgagent.system.registry import ExtractionRegistry
                    registry = ExtractionRegistry()
                    detected_type = registry.detect_type(user_input)

                    # Check if detection found a keyword match
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
                # extraction_type was already set during intent handling
                data = user_input
                is_file_input = False
        else:
            # Not an extract intent, skip extraction logic
            continue

        # Try to extract file path from input (handle cases like "extract from /path/to/file")
        # Look for absolute paths or relative paths
        potential_path = None
        words = data.split()
        for word in words:
            word_path = Path(word)
            if word_path.exists() and word_path.is_file():
                potential_path = word_path
                break

        # Check if input is a file path
        file_path = potential_path or Path(data)
        if file_path.exists() and file_path.is_file():
            print(f"\n📁 Detected file: {file_path}")
            try:
                # Load file
                loaded_data = load_json_file(str(file_path))

                # Check if it's a list (batch processing)
                if isinstance(loaded_data, list):
                    print(f"🔄 Batch processing {len(loaded_data)} items...\n")

                    # Determine output path
                    stem = file_path.stem
                    output_path = file_path.parent / f"{stem}_{extraction_type}_kg.json"

                    # Define format function for results
                    def format_result(result, item, index):
                        formatted = {"index": index}

                        # Add text field from original data
                        if isinstance(item, dict):
                            text = (
                                item.get("text") or
                                item.get("content") or
                                item.get("description") or
                                item.get("body") or
                                str(item)
                            )
                            formatted["text"] = text
                        elif isinstance(item, str):
                            formatted["text"] = item

                        # Format KG based on result type
                        kg = []
                        if "error" in result:
                            formatted["error"] = result["error"]
                        else:
                            # Extract triples/relations - convert to tagged format
                            if "relations" in result:
                                for rel in result["relations"]:
                                    try:
                                        if isinstance(rel, (list, tuple)) and len(rel) == 3:
                                            s, r, o = rel
                                            # Clean values
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
                                            # Clean values
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
                                            # Clean values
                                            s = clean_value(s)
                                            r = clean_value(r)
                                            o = clean_value(o)
                                            kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                                    except (ValueError, TypeError):
                                        continue
                            # For temporal quadruples
                            elif "quadruples" in result:
                                kg = result["quadruples"]
                            # For hyper-relations
                            elif "hyper_relations" in result:
                                kg = result["hyper_relations"]
                            # For AutoSchemaKG events
                            elif "entity_relation_dict" in result or "event_entity_relation_dict" in result:
                                kg = {
                                    "entity_relations": result.get("entity_relation_dict", []),
                                    "event_entities": result.get("event_entity_relation_dict", []),
                                    "event_relations": result.get("event_relation_dict", []),
                                }

                        formatted["kg"] = kg
                        return formatted

                    # Define extraction function
                    async def extract_single(item):
                        return await system.extract_async(
                            data=item,
                            extraction_type=extraction_type,
                        )

                    # Process with resume support
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

                # Check if it's a dict with "tasks" field (batch config format)
                elif isinstance(loaded_data, dict) and "tasks" in loaded_data:
                    tasks_list = loaded_data["tasks"]
                    print(f"🔄 Batch processing {len(tasks_list)} tasks from config...\n")

                    # Determine output path
                    stem = file_path.stem
                    output_path = file_path.parent / f"{stem}_{extraction_type}_kg.json"

                    # Build items list with their extraction types
                    items_with_types = []
                    for task in tasks_list:
                        task_type = task.get("extraction_type", extraction_type)
                        task_data = task.get("data", task)
                        items_with_types.append((task_data, task_type))

                    # Define format function for results
                    def format_result(result, item_tuple, index):
                        item, item_type = item_tuple
                        formatted = {"index": index}

                        # Add text field from original data
                        if isinstance(item, dict):
                            text = (
                                item.get("text") or
                                item.get("content") or
                                item.get("description") or
                                item.get("body") or
                                str(item)
                            )
                            formatted["text"] = text
                        elif isinstance(item, str):
                            formatted["text"] = item

                        # Format KG based on result type
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

                    # Define extraction function
                    async def extract_single(item_tuple):
                        item, item_type = item_tuple
                        return await system.extract_async(
                            data=item,
                            extraction_type=item_type,
                        )

                    # Process with resume support
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
                    # Single item from file
                    print(f"📝 Processing single item...\n")
                    data = loaded_data
                    # Mark that this came from a file
                    is_file_input = True

            except Exception as e:
                print(f"\nError loading file: {e}\n")
                continue
        else:
            # Not a file path
            is_file_input = False

        # Regular extraction
        try:
            # If extraction type is still "auto", try to detect or ask user
            if extraction_type == "auto":
                # Try to detect from data
                from kgagent.system.registry import ExtractionRegistry
                registry = ExtractionRegistry()

                detect_text = data if isinstance(data, str) else ""
                detected_type = registry.detect_type(detect_text)

                # Check if detection found a keyword match or just defaulted
                # If no keyword was found, detected_type will be "triples" (default)
                # We check if any keyword actually matched
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
                    # No keyword detected - ask user
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
                        "": "triples"  # Default
                    }

                    extraction_type = type_map.get(choice, "triples")
                    print(f"✓ 已选择: {extraction_type}\n")

            # Create task
            current_task = asyncio.create_task(
                system.extract_async(
                    data=data,
                    extraction_type=extraction_type,
                )
            )

            # Wait for result
            result = await current_task
            current_task = None

            # Handle output based on input type
            if is_file_input:
                # Save to file (same directory as input)
                output_path = record_result(result, file_path)
                print(f"✓ Extraction complete")
                print(f"📝 Result saved to: {output_path}\n")

                # Record to session history
                chat_session.add_extraction(
                    input_type="file",
                    input_data=str(file_path),
                    extraction_type=extraction_type,
                    result=result,
                    output_path=output_path,
                )
            else:
                # Display in terminal (for direct text input)
                # Use formatted display (same as file output format)
                result_str = format_for_display(result, extraction_type)
                print(f"\nAssistant>\n{result_str}\n")

                # Record to session history
                chat_session.add_extraction(
                    input_type="text",
                    input_data=data,
                    extraction_type=extraction_type,
                    result=result,
                    output_path=None,
                )

        except asyncio.CancelledError:
            print(f"\n⚠️  Extraction cancelled\n")
            current_task = None
        except Exception as e:
            print(f"\nError: {e}\n")
            current_task = None


def main_chat(workspace: str | None = None, model: str | None = None):
    """Synchronous wrapper for chat."""
    asyncio.run(main_chat_async(workspace, model))
