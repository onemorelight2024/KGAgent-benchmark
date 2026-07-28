"""Interactive chat interface for KGAgent."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kgagent.system import KGAgentSystem
from kgagent.extraction.tools.loaders import load_json_file, save_json_file
from kgagent.output_process import record_result, record_batch_results, format_for_display
from kgagent.output_process.record import format_single_result, clean_value, is_valid_triple_value
from kgagent.extraction.config import ExtractionConfig
from kgagent.intent import IntentEntry
from kgagent.core.session import ChatSession
from kgagent.core.batch import process_batch_with_resume
from kgagent.core.config import get_model
from kgagent.benchmark.chat_flow import looks_like_benchmark_request
from kgagent.benchmark.conversation import BenchmarkConversation

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

    chat_model = model or get_model()

    # Create system
    system = KGAgentSystem(
        model_name=chat_model,
        work_dir=workspace or "./tmp_sdk",
    )

    # Create intent agent
    intent_config = ExtractionConfig(
        model_name=chat_model,
        work_dir=workspace or "./tmp_sdk",
        permission_mode="auto",
        max_turns=1,
    )
    intent_agent = IntentEntry(intent_config)

    # Create chat session for memory
    chat_session = ChatSession(max_history=10)
    print(f"💾 Session memory enabled (keeping last 10 extractions)\n")
    benchmark_conversation = BenchmarkConversation(
        workspace=Path(workspace or ".").resolve(),
        sdk_model=chat_model,
        benchmark_model="gpt-4o-mini",
    )

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

        # Benchmark is a multi-turn agent workflow. The benchmark conversation
        # agent interprets natural replies and updates the workflow state.
        if benchmark_conversation.active or looks_like_benchmark_request(user_input):
            if benchmark_conversation.active:
                message, benchmark_params = await benchmark_conversation.handle(user_input)
            else:
                message, benchmark_params = await benchmark_conversation.start(user_input)
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
                print("\nAssistant> benchmark 生成完成。\n")
                print(f"| 项目 | 结果 |")
                print(f"|---|---|")
                print(f"| 类型 | {result.get('benchmark_type')} / {result.get('graph_type')} |")
                print(f"| 方法 | {result.get('method')} |")
                print(f"| 模型 | {result.get('model')} |")
                print(f"| 样本数 | {stats.get('valid', 0)} / {stats.get('total', 0)} 有效 |")
                print(f"| 输出文件 | `{output_path}` |")
                print()

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
            print("  - 生成 benchmark: 例如 '我想做 KGQA benchmark' 或 kgagent benchmark examples/kg_benchmark_input.json")
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

        # Handle convert intent
        if intent_result and intent_result.get("intent") == "convert":
            params = intent_result.get("parameters", {})
            source = params.get("source", "last")
            target_format = params.get("target_format", "neo4j_csv")
            output_path = params.get("output_path")

            try:
                # Get source data
                if source == "last":
                    last_record = chat_session.get_last_extraction()
                    if last_record is None:
                        print("⚠️  没有可转换的抽取结果\n")
                        continue

                    # Format the last result for conversion
                    from kgagent.output_process.record import format_single_result
                    formatted = format_single_result(
                        last_record.result,
                        input_data=last_record.input_data,
                        extraction_type=last_record.extraction_type,
                    )
                    source_data = formatted
                elif source.startswith("file:"):
                    source_path = source[5:]
                    source_data = source_path  # Let converter load the file
                else:
                    print(f"⚠️  不支持的数据源: {source}\n")
                    continue

                # Import conversion module
                from kgagent.system.orchestrator import run_conversion

                # Run conversion
                print(f"\n🔄 转换格式: {target_format}...")
                result = await run_conversion(
                    source=source,
                    target_format=target_format,
                    output_path=output_path,
                    last_result=source_data if source == "last" else None,
                )

                # Display result
                if "output_dir" in result:
                    print(f"✓ 转换完成")
                    print(f"📁 输出目录: {result['output_dir']}")

                    # Display files (for Neo4j CSV or GraphML with multiple indices)
                    if "files" in result:
                        print(f"📄 生成的文件:")
                        for file_info in result['files']:
                            if 'nodes_file' in file_info:
                                # Neo4j CSV format
                                print(f"  - Index {file_info['index']}: {file_info['nodes']} 节点, {file_info['relationships']} 关系")
                            elif 'file' in file_info:
                                # GraphML format
                                print(f"  - Index {file_info['index']}: {file_info['file']} ({file_info['nodes']} 节点, {file_info['edges']} 边)")

                    # Legacy single file format
                    if "nodes_file" in result:
                        print(f"  - 节点文件: {result['nodes_file']}")
                    if "relationships_file" in result:
                        print(f"  - 关系文件: {result['relationships_file']}")

                    print(f"📊 统计: {result.get('statistics', {})}\n")
                elif "output_file" in result:
                    print(f"✓ 转换完成")
                    print(f"📝 输出文件: {result['output_file']}\n")
                else:
                    print(f"✓ 转换完成\n")

            except Exception as e:
                print(f"⚠️  转换失败: {e}\n")
                import traceback
                traceback.print_exc()

            continue

        # === Step 3: Handle import intent ===

        # Handle import intent (convert from external formats to JSON)
        if intent_result and intent_result.get("intent") == "import":
            params = intent_result.get("parameters", {})
            input_path = params.get("input_path")
            output_path = params.get("output_path")

            if not input_path:
                print("⚠️  未指定输入文件路径\n")
                continue

            try:
                print(f"\n🔄 导入文件: {input_path}")
                print(f"✓ 意图: {intent_result.get('explanation', '导入外部格式')}")

                # Use system.convert_from to import
                result = system.convert_from(
                    input_path=input_path,
                    output_path=output_path,
                )

                # Display result
                print(f"✓ 导入完成")
                print(f"📝 输出文件: {result['output_file']}")

                if 'statistics' in result:
                    stats = result['statistics']
                    print(f"📊 统计:")
                    if 'nodes' in stats:
                        print(f"  - 节点数: {stats['nodes']}")
                    if 'relations' in stats:
                        print(f"  - 关系数: {stats['relations']}")

                # Load and add to session memory
                output_file = Path(result['output_file'])
                if output_file.exists():
                    with open(output_file, 'r', encoding='utf-8') as f:
                        imported_data = json.load(f)

                    chat_session.add_extraction(
                        input_data=input_path,
                        input_type="file",
                        extraction_type="imported",
                        result=imported_data,
                    )
                    print(f"💾 已添加到会话记忆\n")
                else:
                    print()

            except FileNotFoundError as e:
                print(f"⚠️  文件未找到: {e}\n")
            except RuntimeError as e:
                print(f"⚠️  导入失败: {e}")
                print(f"提示: Neo4j dump文件需要安装Neo4j工具 (neo4j-admin, cypher-shell)\n")
            except Exception as e:
                print(f"⚠️  导入失败: {e}\n")
                import traceback
                traceback.print_exc()

            continue

        # === Step 4: Handle parse intent ===

        # Handle parse intent (document parsing with MinerU)
        if intent_result and intent_result.get("intent") == "parse":
            params = intent_result.get("parameters", {})
            input_path = params.get("input_path")
            output_path = params.get("output_path")
            next_action = params.get("next_action")  # e.g., "extract_triples"

            if not input_path:
                print("⚠️  未指定输入文件路径\n")
                continue

            try:
                from kgagent.mineru import is_parseable_document, get_document_type

                # Check if file is parseable (exclude JSON files)
                if input_path.lower().endswith('.json'):
                    print(f"⚠️  JSON 文件应该直接提取，不需要解析")
                    print(f"请使用: extract [type] from {input_path}\n")
                    continue

                if not is_parseable_document(input_path):
                    print(f"⚠️  不支持的文件格式: {input_path}")
                    print(f"支持的格式: PDF, DOCX, PPTX, PNG, JPG\n")
                    continue

                doc_type = get_document_type(input_path)

                # Detect extraction type from original input
                extraction_type = "triples"  # default
                if "temporal" in user_input.lower():
                    extraction_type = "temporal"
                elif "hyper" in user_input.lower():
                    extraction_type = "hyper"
                elif "event" in user_input.lower():
                    extraction_type = "event"

                # Check if user wants direct extraction (contains "extract" keyword)
                wants_extraction = "extract" in user_input.lower()

                if wants_extraction:
                    # Direct extraction workflow with preprocessing
                    print(f"\n📄 检测到 {doc_type.upper()} 文档")
                    print(f"🔄 开始预处理和提取...\n")

                    from kgagent.extraction import preprocess_document, process_pdf_with_type

                    # Define output paths
                    input_file = Path(input_path)
                    output_dir = input_file.parent
                    input_stem = input_file.stem

                    # Step 1: Preprocess document
                    print(f"[1/4] 预处理文档...")
                    preprocess_result = await preprocess_document(
                        input_path,
                        chunk_size=2000,
                        overlap=200
                    )

                    if not preprocess_result.get("success"):
                        print(f"✗ 预处理失败: {preprocess_result.get('error')}\n")
                        continue

                    chunks = []

                    # Check if using cached chunks
                    if preprocess_result.get("cached"):
                        print(f"✓ 使用缓存的 chunks: {len(preprocess_result['chunks'])} 个")
                        chunks = preprocess_result["chunks"]

                        # Skip to extraction directly
                    else:
                        # Step 2: Handle PDF type confirmation if needed
                        if preprocess_result.get("requires_user_confirmation"):
                            print(f"\n[2/4] 确认 PDF 类型")
                            print(preprocess_result["message"])

                            try:
                                pdf_type = (await get_input("\nPDF 类型 (text_only/mixed_content): ")).strip().lower()
                            except KeyboardInterrupt:
                                print("\n⚠️  已取消\n")
                                continue

                            if pdf_type not in ["text_only", "mixed_content"]:
                                print(f"⚠️  无效的类型，默认使用 text_only\n")
                                pdf_type = "text_only"

                            # Process PDF with confirmed type
                            chunk_result = process_pdf_with_type(
                                preprocess_result,
                                pdf_type,
                                chunk_size=2000,
                                overlap=200
                            )

                            if not chunk_result["success"]:
                                print(f"✗ 处理失败: {chunk_result['error']}\n")
                                continue

                            chunks = chunk_result["chunks"]
                        else:
                            chunks = preprocess_result.get("chunks", [])

                        print(f"\n✓ 文档切分完成: {len(chunks)} 个 chunks")

                        # Save chunks JSON (only if not cached)
                        chunks_file = output_dir / f"{input_stem}_chunks.json"
                        with open(chunks_file, "w", encoding="utf-8") as f:
                            json.dump(chunks, f, indent=2, ensure_ascii=False)
                        print(f"💾 Chunks 已保存: {chunks_file}")

                    # Step 3: Extract from each chunk with resume support
                    print(f"\n[3/4] 提取知识图谱 (类型: {extraction_type})")

                    # Output file for KG results (include extraction type in filename)
                    kg_output_file = output_dir / f"{input_stem}_{extraction_type}_kg.json"

                    # Use batch processing with resume
                    from kgagent.core.batch import process_batch_with_resume

                    # Define processing function for each chunk
                    async def process_chunk(chunk: dict) -> dict:
                        result = await system.extract_async(
                            data=chunk["text"],
                            extraction_type=extraction_type,
                        )
                        return result

                    # Define format function
                    def format_chunk_result(result, chunk, index):
                        formatted = {
                            "index": index,
                            "chunk_index": chunk["index"],
                            "chunk_text_preview": chunk["text"][:100] + "..." if len(chunk["text"]) > 100 else chunk["text"],
                        }
                        formatted.update(result)
                        return formatted

                    # Process with resume support
                    print(f"📊 使用批量处理模式（支持断点继续）")
                    print("-" * 50)

                    current_task = asyncio.create_task(
                        process_batch_with_resume(
                            items=chunks,
                            process_fn=process_chunk,
                            output_path=kg_output_file,
                            max_concurrent=3,  # Process 3 chunks at a time
                            format_fn=format_chunk_result,
                        )
                    )

                    try:
                        batch_result = await current_task
                    except asyncio.CancelledError:
                        print("\n⚠️  PDF extraction cancelled\n")
                        current_task = None
                        continue
                    finally:
                        current_task = None

                    # Step 4: Display summary
                    print("\n" + "=" * 50)
                    print(f"[4/4] 提取完成!")
                    print(f"  - 文档: {Path(input_path).name}")
                    print(f"  - Chunks 总数: {batch_result['total']}")
                    print(f"  - 已完成: {batch_result['completed']}")
                    print(f"  - 跳过（已存在）: {batch_result['skipped']}")
                    print(f"  - 失败: {batch_result['failed']}")

                    # Load results to calculate stats
                    with open(kg_output_file, "r", encoding="utf-8") as f:
                        all_results = json.load(f)

                    total_kg = sum(len(r.get("kg", [])) for r in all_results)
                    total_entities = sum(len(r.get("entities", [])) for r in all_results)

                    print(f"  - 实体: {total_entities}")
                    print(f"  - 知识图谱项: {total_kg}")

                    # Step 5: Merge chunks into single graph
                    print(f"\n[5/5] 合并图谱...")
                    try:
                        from kgagent.extraction.merge import merge_kg_chunks

                        merge_result = await merge_kg_chunks(
                            kg_file=kg_output_file,
                            extraction_type=extraction_type,
                            llm_client=system,
                        )

                        print(f"✓ 图谱合并完成")
                        if merge_result.get("disambiguation"):
                            print(f"  - 合并后图谱项: {merge_result['total_items']}")
                            print(f"  - 实体: {merge_result['entities']} (消歧: {merge_result['entities_merged']})")
                            print(f"  - 关系: {merge_result['relations']} (消歧: {merge_result['relations_merged']})")
                        else:
                            print(f"  - 事件类型，简单合并")
                        print(f"  - 输出文件: {merge_result['output_file']}")
                    except Exception as e:
                        print(f"⚠️  图谱合并失败: {e}")
                        logger.warning(f"Failed to merge KG chunks: {e}")

                    print(f"\n💾 已保存文件:")

                    # List all saved files
                    md_file = preprocess_result.get("parse_result", {}).get("markdown_file")
                    if md_file:
                        print(f"  - Markdown: {md_file}")
                    print(f"  - Chunks: {chunks_file}")
                    print(f"  - 知识图谱 (chunks): {kg_output_file}")
                    if merge_result and merge_result.get("output_file"):
                        print(f"  - 知识图谱 (合并): {merge_result['output_file']}")
                    print()

                    # Update session history
                    chat_session.add_extraction(
                        input_type="file",
                        input_data=str(input_path),
                        extraction_type=extraction_type,
                        result={"results": all_results, "total_chunks": len(chunks)},
                        output_path=str(kg_output_file),
                    )

                    continue

                # Otherwise, just parse the document (old behavior)

                # Check token to determine mode
                import os
                token = os.getenv("MINERU_API_TOKEN")
                mode = "精确模式" if token else "轻量模式"

                # Show confirmation prompt
                print(f"\n检测到{doc_type.upper()}文档，需要先解析为文本。")
                print(f"📄 文件: {input_path}")
                print(f"🔧 解析工具: MinerU ({mode})")

                # Determine output path
                from kgagent.mineru.detector import get_output_path
                actual_output = get_output_path(input_path, output_path)
                print(f"📝 输出: {actual_output}")

                # Ask for confirmation
                try:
                    confirm = (await get_input("\n是否继续？[Y/n]: ")).strip().lower()
                except KeyboardInterrupt:
                    print("\n⚠️  已取消\n")
                    continue

                if confirm == 'n':
                    print("已取消\n")
                    continue

                # Parse document
                print(f"\n🔄 正在解析文档...")

                result = await system.parse_document_async(
                    input_path=input_path,
                    output_path=output_path,
                )

                if result.get('success'):
                    print(f"✓ 解析完成: {Path(result['output_file']).name}", end="")
                    if 'pages' in result:
                        print(f" ({result['pages']}页)")
                    else:
                        print()

                    print(f"💾 已保存到: {result['output_file']}")

                    # If next_action is specified, ask if user wants to continue
                    if next_action:
                        print(f"\n检测到后续操作: {next_action}")
                        try:
                            continue_extract = (await get_input("是否继续抽取知识图谱？[Y/n]: ")).strip().lower()
                            if continue_extract != 'n':
                                # Load parsed markdown and continue to extraction
                                user_input = f"extract triples from {result['output_file']}"
                                print(f"\n继续执行: {user_input}\n")
                                continue
                        except KeyboardInterrupt:
                            print("\n")

                    print()
                else:
                    print(f"✗ 解析失败")
                    if 'error' in result:
                        print(f"错误: {result['error']}\n")

            except FileNotFoundError as e:
                print(f"⚠️  文件未找到: {e}\n")
            except Exception as e:
                print(f"⚠️  解析失败: {e}\n")
                import traceback
                traceback.print_exc()

            continue

        # === Step 5: Handle extract intent ===

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
            try:
                confirm = (await get_input("\n确认执行? [Y/n/edit]: ")).strip().lower()
            except KeyboardInterrupt:
                print("\n⚠️  已取消\n")
                continue

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
                try:
                    choice = (await get_input("请输入选项 (1/2/3/4): ")).strip()
                except KeyboardInterrupt:
                    print("\n⚠️  已取消\n")
                    continue
                type_map = {"1": "triples", "2": "temporal", "3": "hyper", "4": "event"}
                extraction_type = type_map.get(choice, params.get('extraction_type', 'auto'))
            else:
                # User confirmed, use detected type
                extraction_type = params.get('extraction_type', 'auto')

            # Prepare data
            if params.get("file_path"):
                file_path = params['file_path']

                # Check if file is a document that needs preprocessing
                from kgagent.mineru import is_parseable_document, get_document_type

                if is_parseable_document(file_path):
                    doc_type = get_document_type(file_path)

                    # Use preprocessing workflow for PDF/documents
                    print(f"\n📄 检测到 {doc_type.upper()} 文档")
                    print(f"🔄 开始预处理和提取...")

                    try:
                        from kgagent.extraction import preprocess_document, process_pdf_with_type

                        # Step 1: Preprocess document
                        print(f"\n[1/3] 预处理文档...")
                        preprocess_result = await preprocess_document(
                            file_path,
                            chunk_size=2000,
                            overlap=200
                        )

                        if not preprocess_result.get("success"):
                            print(f"✗ 预处理失败: {preprocess_result.get('error')}\n")
                            continue

                        chunks = []

                        # Step 2: Handle PDF type confirmation if needed
                        if preprocess_result.get("requires_user_confirmation"):
                            print(f"\n[2/3] 确认 PDF 类型")
                            print(preprocess_result["message"])

                            try:
                                pdf_type = (await get_input("\nPDF 类型 (text_only/mixed_content): ")).strip().lower()
                            except KeyboardInterrupt:
                                print("\n⚠️  已取消\n")
                                continue

                            if pdf_type not in ["text_only", "mixed_content"]:
                                print(f"⚠️  无效的类型，默认使用 text_only\n")
                                pdf_type = "text_only"

                            # Process PDF with confirmed type
                            chunk_result = process_pdf_with_type(
                                preprocess_result,
                                pdf_type,
                                chunk_size=2000,
                                overlap=200
                            )

                            if not chunk_result["success"]:
                                print(f"✗ 处理失败: {chunk_result['error']}\n")
                                continue

                            chunks = chunk_result["chunks"]
                        else:
                            chunks = preprocess_result.get("chunks", [])

                        print(f"\n✓ 文档切分完成: {len(chunks)} 个 chunks")

                        # Step 3: Extract from each chunk
                        print(f"\n[3/3] 提取知识图谱 (类型: {extraction_type})")
                        print("-" * 50)

                        all_results = []
                        for i, chunk in enumerate(chunks, 1):
                            print(f"处理 {chunk['index']} ({i}/{len(chunks)})...", end=" ")

                            try:
                                result = await system.extract_async(
                                    data=chunk["text"],
                                    extraction_type=extraction_type,
                                )

                                result["_chunk_index"] = chunk["index"]
                                result["_chunk_order"] = i
                                all_results.append(result)

                                # Show brief stats
                                if extraction_type == "triples" and "triples" in result:
                                    print(f"✓ {len(result['triples'])} 三元组")
                                elif extraction_type == "event" and "events" in result:
                                    print(f"✓ {len(result['events'])} 事件")
                                else:
                                    print("✓")
                            except Exception as e:
                                print(f"✗ {e}")
                                all_results.append({
                                    "_chunk_index": chunk["index"],
                                    "_chunk_order": i,
                                    "error": str(e),
                                })

                        # Display summary
                        print("\n" + "=" * 50)
                        total_kg = sum(len(r.get("kg", [])) for r in all_results)
                        total_entities = sum(len(r.get("entities", [])) for r in all_results)

                        print(f"✓ 提取完成!")
                        print(f"  - 文档: {Path(file_path).name}")
                        print(f"  - Chunks: {len(chunks)}")
                        print(f"  - 实体: {total_entities}")
                        print(f"  - 知识图谱项: {total_kg}")

                        # Save results
                        output_file = Path(file_path).parent / f"{Path(file_path).stem}_kg.json"
                        with open(output_file, "w", encoding="utf-8") as f:
                            json.dump(all_results, f, indent=2, ensure_ascii=False)
                        print(f"  - 已保存到: {output_file}")
                        print()

                        # Update session history
                        session.add_result(all_results)

                        continue

                    except Exception as e:
                        print(f"✗ 处理失败: {e}\n")
                        import traceback
                        traceback.print_exc()
                        continue

                user_input = file_path
                # Note: extraction_type is already set from intent, don't add --type flag
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
                            # Check for "triple" field (singular, standard format)
                            if "triple" in result:
                                kg = result["triple"]  # Already in tagged format
                            elif "relations" in result:
                                for rel in result["relations"]:
                                    try:
                                        if isinstance(rel, (list, tuple)) and len(rel) == 3:
                                            s, r, o = rel
                                            # Validate triple values
                                            if not (is_valid_triple_value(s) and is_valid_triple_value(r) and is_valid_triple_value(o)):
                                                continue
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
                                            # Validate triple values
                                            if not (is_valid_triple_value(s) and is_valid_triple_value(r) and is_valid_triple_value(o)):
                                                continue
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
                                            # Validate triple values
                                            if not (is_valid_triple_value(s) and is_valid_triple_value(r) and is_valid_triple_value(o)):
                                                continue
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

                    # Add to session memory
                    chat_session.add_extraction(
                        input_type="file",
                        input_data=str(file_path),
                        extraction_type=extraction_type,
                        result={"status": "completed", "items": stats['completed']},
                        output_path=stats['output_path'],
                    )

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

                    # Add to session memory
                    chat_session.add_extraction(
                        input_type="file",
                        input_data=str(file_path),
                        extraction_type=extraction_type,
                        result={"status": "completed", "items": stats['completed']},
                        output_path=stats['output_path'],
                    )

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

                    try:
                        choice = (await get_input("请输入选项 (1/2/3/4，直接回车默认为 triples): ")).strip()
                    except KeyboardInterrupt:
                        print("\n⚠️  已取消\n")
                        continue

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

        except KeyboardInterrupt:
            print(f"\n⚠️  Extraction cancelled by user\n")
            if current_task and not current_task.done():
                current_task.cancel()
                try:
                    await current_task
                except asyncio.CancelledError:
                    pass
            current_task = None
        except asyncio.CancelledError:
            print(f"\n⚠️  Extraction cancelled\n")
            current_task = None
        except Exception as e:
            print(f"\nError: {e}\n")
            current_task = None


def main_chat(workspace: str | None = None, model: str | None = None):
    """Synchronous wrapper for chat."""
    asyncio.run(main_chat_async(workspace, model))
