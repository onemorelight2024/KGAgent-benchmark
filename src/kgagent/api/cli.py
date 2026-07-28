"""Command-line interface for KGAgent."""

from __future__ import annotations

import argparse
import json
import sys

from kgagent.system import KGAgentSystem


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="KGAgent - Knowledge Graph Extraction Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Chat command
    chat_parser = subparsers.add_parser("chat", help="Start interactive chat")
    chat_parser.add_argument(
        "--workspace",
        "-w",
        default=None,
        help="Working directory (default: ./tmp_sdk)",
    )
    chat_parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="Model name (default: from config)",
    )

    # Extract command
    extract_parser = subparsers.add_parser("extract", help="Extract knowledge graph")
    extract_parser.add_argument(
        "data",
        help="Input data (file path or inline text)",
    )
    extract_parser.add_argument(
        "--type",
        "-t",
        choices=["auto", "triples", "temporal", "hyper"],
        default="auto",
        help="Extraction type (default: auto)",
    )
    extract_parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output file path",
    )
    extract_parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate result",
    )
    extract_parser.add_argument(
        "--workspace",
        "-w",
        default=None,
        help="Working directory",
    )
    extract_parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="Model name",
    )

    # Batch command
    batch_parser = subparsers.add_parser("batch", help="Batch extraction")
    batch_parser.add_argument(
        "config",
        help="JSON config file with tasks",
    )
    batch_parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=4,
        help="Max concurrency (default: 4)",
    )
    batch_parser.add_argument(
        "--workspace",
        "-w",
        default=None,
        help="Working directory",
    )
    batch_parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="Model name",
    )

    # Convert command
    convert_parser = subparsers.add_parser("convert", help="Convert KG format")
    convert_parser.add_argument(
        "input",
        help="Input file path (JSON KG data)",
    )
    convert_parser.add_argument(
        "format",
        choices=["neo4j_csv", "neo4j", "graphml", "rdf", "json"],
        help="Output format",
    )
    convert_parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output path (file or directory)",
    )

    # Import command (convert from external formats)
    import_parser = subparsers.add_parser("import", help="Import from external format")
    import_parser.add_argument(
        "input",
        help="Input file path (.dump, .graphml, etc.)",
    )
    import_parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output JSON file path",
    )
    import_parser.add_argument(
        "--neo4j-home",
        default=None,
        help="Neo4j home directory (for .dump files)",
    )

    # Parse command (document parsing with MinerU)
    parse_parser = subparsers.add_parser("parse", help="Parse document to Markdown")
    parse_parser.add_argument(
        "input",
        help="Input document path (PDF, DOCX, PPTX, etc.)",
    )
    parse_parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output markdown file path",
    )

    # Benchmark command
    benchmark_parser = subparsers.add_parser("benchmark", help="Generate KGQA/KGQG benchmark")
    benchmark_parser.add_argument("data", help="Input KG/TKG JSON path or inline JSON")
    benchmark_parser.add_argument(
        "--graph-type",
        choices=["KG", "TKG"],
        default="KG",
        help="Input graph type (default: KG)",
    )
    benchmark_parser.add_argument(
        "--task",
        choices=["KGQA", "KGQG", "temporal_KGQA", "temporal_KGQG"],
        default="KGQA",
        help="Benchmark task (default: KGQA)",
    )
    benchmark_parser.add_argument(
        "--method",
        default="sgsh_prompt",
        help="Generation method (default: sgsh_prompt)",
    )
    benchmark_parser.add_argument(
        "--sample-count",
        "-n",
        type=int,
        default=5,
        help="Number of samples to generate (default: 5)",
    )
    benchmark_parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL")
    benchmark_parser.add_argument("--api-key", default=None, help="API key")
    benchmark_parser.add_argument("--output", "-o", default=None, help="Output JSONL path")
    benchmark_parser.add_argument("--workspace", "-w", default=None, help="Working directory")
    benchmark_parser.add_argument("--model", "-m", default="gpt-4o-mini", help="Model name")

    args = parser.parse_args()

    if args.command == "chat":
        from kgagent.api.chat import main_chat

        main_chat(workspace=args.workspace, model=args.model)

    elif args.command == "extract":
        from pathlib import Path

        # Generate default output path if not specified
        output_path = args.output
        if output_path is None and Path(args.data).exists() and Path(args.data).is_file():
            # Auto-generate output filename: input_file_{type}_kg.json
            input_path = Path(args.data)
            extraction_type = args.type if args.type != "auto" else "triples"
            output_filename = f"{input_path.stem}_{extraction_type}_kg.json"
            output_path = str(input_path.parent / output_filename)
            print(f"No output specified, using: {output_path}")

        system = KGAgentSystem(
            model_name=args.model,
            work_dir=args.workspace or "./tmp_sdk",
        )

        result = system.extract(
            data=args.data,
            extraction_type=args.type,
            validate=args.validate,
            save_to=output_path,
        )

        # Print result only if no output file (inline text input)
        if output_path is None:
            print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.command == "batch":
        import asyncio

        # Load tasks from config file
        with open(args.config, "r") as f:
            config = json.load(f)

        tasks = config.get("tasks", [])
        if not tasks:
            print("Error: No tasks found in config file", file=sys.stderr)
            sys.exit(1)

        system = KGAgentSystem(
            model_name=args.model,
            work_dir=args.workspace or "./tmp_sdk",
        )

        results = asyncio.run(
            system.extract_batch(tasks, max_concurrency=args.concurrency)
        )

        # Print results
        print(json.dumps(results, indent=2, ensure_ascii=False))

    elif args.command == "convert":
        from pathlib import Path

        system = KGAgentSystem()

        result = system.convert(
            input_data=args.input,
            output_format=args.format,
            output_path=args.output,
        )

        print(f"Conversion complete:")
        print(f"  Format: {result.get('format')}")
        if 'output_file' in result:
            print(f"  Output: {result['output_file']}")
        elif 'output_dir' in result:
            print(f"  Output: {result['output_dir']}")

        if 'statistics' in result:
            print(f"  Statistics: {result['statistics']}")

    elif args.command == "import":
        from pathlib import Path

        system = KGAgentSystem()

        result = system.convert_from(
            input_path=args.input,
            output_path=args.output,
            neo4j_home=getattr(args, 'neo4j_home', None),
        )

        print(f"Import complete:")
        print(f"  Format: {result.get('format')}")
        print(f"  Output: {result.get('output_file')}")

        if 'statistics' in result:
            print(f"  Statistics: {result['statistics']}")

    elif args.command == "parse":
        from pathlib import Path

        system = KGAgentSystem()

        print(f"Parsing document: {args.input}")

        try:
            result = system.parse_document(
                input_path=args.input,
                output_path=args.output,
            )

            if result.get('success'):
                print(f"\n✓ Parsing complete:")
                print(f"  Input: {result['input_file']}")
                print(f"  Output: {result['output_file']}")
                print(f"  Format: {result['format']}")
                print(f"  Mode: {result['mode']}")

                if 'pages' in result:
                    print(f"  Pages: {result['pages']}")

                if 'size' in result:
                    print(f"  Size: {result['size']} bytes")
            else:
                print(f"\n✗ Parsing failed:")
                if 'error' in result:
                    print(f"  Error: {result['error']}")
                sys.exit(1)

        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "benchmark":
        system = KGAgentSystem(
            model_name=args.model,
            work_dir=args.workspace or ".",
        )
        result = system.benchmark(
            data=args.data,
            graph_type=args.graph_type,
            task=args.task,
            method=args.method,
            sample_count=args.sample_count,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            output_path=args.output,
        )
        safe_result = dict(result)
        print(json.dumps(safe_result, indent=2, ensure_ascii=False))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
