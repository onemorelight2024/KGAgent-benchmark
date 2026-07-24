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

    args = parser.parse_args()

    if args.command == "chat":
        from kgagent.api.chat import main_chat

        main_chat(workspace=args.workspace, model=args.model)

    elif args.command == "extract":
        system = KGAgentSystem(
            model_name=args.model,
            work_dir=args.workspace or "./tmp_sdk",
        )

        result = system.extract(
            data=args.data,
            extraction_type=args.type,
            validate=args.validate,
            save_to=args.output,
        )

        # Print result
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

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
