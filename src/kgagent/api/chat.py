"""Interactive chat interface for KGAgent."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kgagent.system import KGAgentSystem
from kgagent.extraction.tools.loaders import load_json_file
from kgagent.extraction.record import record_result, record_batch_results

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

    # Setup prompt session if available
    if PROMPT_TOOLKIT_AVAILABLE:
        history = InMemoryHistory()
        bindings = KeyBindings()

        @bindings.add('c-c')
        def _(event):
            """Handle Ctrl+C - cancel current operation but stay in chat."""
            event.app.exit(exception=KeyboardInterrupt)

        session = PromptSession(history=history, key_bindings=bindings)

        async def get_input(prompt: str) -> str:
            """Get input with prompt_toolkit support (async)."""
            try:
                return await session.prompt_async(prompt)
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
                print("\n⚠️  Task cancelled")
                current_task.cancel()
                try:
                    await current_task
                except asyncio.CancelledError:
                    pass
                current_task = None
                continue
            else:
                print("\nUse :quit to exit")
                continue
        except (EOFError, KeyboardInterrupt):
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
            print("  - Results from files are auto-saved to same directory\n")
            continue

        # Parse input
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

                    # Build batch tasks - use the detected/specified extraction_type
                    tasks = [
                        {
                            "data": item,
                            "extraction_type": extraction_type,
                        }
                        for item in loaded_data
                    ]

                    # Batch extract
                    results = await system.extract_batch(tasks, max_concurrency=4)

                    # Save results to file
                    output_path = record_batch_results(
                        results,
                        file_path,
                        input_data=loaded_data,
                        extraction_type=extraction_type
                    )
                    print(f"✓ Processed {len(results)} items")
                    print(f"📝 Results saved to: {output_path}\n")
                    continue

                # Check if it's a dict with "tasks" field (batch config format)
                elif isinstance(loaded_data, dict) and "tasks" in loaded_data:
                    tasks_list = loaded_data["tasks"]
                    print(f"🔄 Batch processing {len(tasks_list)} tasks from config...\n")

                    # Build batch tasks (each task already has extraction_type)
                    tasks = []
                    original_data = []
                    for task in tasks_list:
                        task_type = task.get("extraction_type", extraction_type)
                        task_data = task.get("data", task)
                        tasks.append({
                            "data": task_data,
                            "extraction_type": task_type,
                        })
                        original_data.append(task_data)

                    # Batch extract
                    results = await system.extract_batch(tasks, max_concurrency=4)

                    # Save results to file
                    output_path = record_batch_results(
                        results,
                        file_path,
                        input_data=original_data,
                        extraction_type=extraction_type
                    )
                    print(f"✓ Processed {len(results)} tasks")
                    print(f"📝 Results saved to: {output_path}\n")
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
            else:
                # Display in terminal (for direct text input)
                result_str = json.dumps(result, indent=2, ensure_ascii=False)
                print(f"\nAssistant>\n{result_str}\n")

        except asyncio.CancelledError:
            print(f"\n⚠️  Extraction cancelled\n")
            current_task = None
        except Exception as e:
            print(f"\nError: {e}\n")
            current_task = None


def main_chat(workspace: str | None = None, model: str | None = None):
    """Synchronous wrapper for chat."""
    asyncio.run(main_chat_async(workspace, model))
