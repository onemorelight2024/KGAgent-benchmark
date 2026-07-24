"""Interactive chat interface for KGAgent."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kgagent.system import KGAgentSystem
from kgagent.extraction.tools.loaders import load_json_file
from kgagent.extraction.record import record_result, record_batch_results


async def main_chat_async(workspace: str | None = None, model: str | None = None):
    """Run interactive chat interface.

    Args:
        workspace: Working directory (default: ./kg_outputs)
        model: Model name (default: from config)
    """
    print("KGAgent - Knowledge Graph Extraction")
    print("=" * 50)
    print()
    print("Commands:")
    print("  Type your extraction request or file path")
    print("  choose/select/选择 - Interactive type selection")
    print("  :quit or :exit - Exit the chat")
    print("  :help - Show help")
    print()
    print("Examples:")
    print("  extract triples: Alice works at Acme")
    print("  examples/data.json")
    print("  /path/to/file.json --type hyper")
    print("  choose  (then follow prompts)")
    print()

    # Create system
    system = KGAgentSystem(
        model_name=model,
        work_dir=workspace or "./kg_outputs",
    )

    while True:
        try:
            user_input = input("User> ").strip()
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
            print("  - Type 'choose' or '选择' for interactive type selection")
            print("  - Supported types: triples, temporal, hyper, auto")
            print("  - Multi-item files will be batch processed")
            print("  - Results from files are auto-saved to same directory\n")
            continue

        # Parse input
        extraction_type = "auto"
        data = user_input
        is_file_input = False

        # Check if user wants to choose extraction type interactively
        if user_input.lower() in ["choose", "select", "选择类型", "选择"]:
            print("\n请选择抽取类型：")
            print("  1. triples - 关系三元组 (subject, relation, object)")
            print("  2. temporal - 时序四元组 (subject, relation, object, time)")
            print("  3. hyper - 超关系 (relation with attributes)")
            print()

            choice = input("请输入选项 (1/2/3): ").strip()

            type_map = {
                "1": "triples",
                "2": "temporal",
                "3": "hyper"
            }

            extraction_type = type_map.get(choice, "triples")
            print(f"✓ 已选择: {extraction_type}\n")

            # Ask for data input
            data_input = input("请输入文本或文件路径: ").strip()
            if not data_input:
                print("未输入数据，已取消\n")
                continue

            user_input = data_input
            data = data_input

        # Check if input contains --type flag
        if "--type" in user_input:
            parts = user_input.split("--type")
            data = parts[0].strip()
            type_part = parts[1].strip().split()[0]
            extraction_type = type_part

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

                    # Build batch tasks
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
                        input_data=loaded_data
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
                        input_data=original_data
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
                    print()

                    choice = input("请输入选项 (1/2/3，直接回车默认为 triples): ").strip()

                    type_map = {
                        "1": "triples",
                        "2": "temporal",
                        "3": "hyper",
                        "": "triples"  # Default
                    }

                    extraction_type = type_map.get(choice, "triples")
                    print(f"✓ 已选择: {extraction_type}\n")

            result = await system.extract_async(
                data=data,
                extraction_type=extraction_type,
            )

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

        except Exception as e:
            print(f"\nError: {e}\n")


def main_chat(workspace: str | None = None, model: str | None = None):
    """Synchronous wrapper for chat."""
    asyncio.run(main_chat_async(workspace, model))
