from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from tkgqg_shared import (
    ALLEN_INFO,
    MULTI_EVENT_INFO,
    TEMPORAL_TKGQG_DIR,
    TRACE_SAMPLES_PATH,
    build_benchmark_record,
    load_jsonl,
    sample_priority,
    write_json,
    write_jsonl,
)


def _allen_tc_count(sample: dict) -> int:
    """Count only Allen pairwise temporal constraints (excludes multi_event_type)."""
    return sum(
        1 for s in sample["steps"]
        if s["step_kind"] == "temporal_filter"
        and "allen_code" in (s.get("constraint_payload") or {})
    )


def _tc_count(sample: dict) -> int:
    """Count ALL temporal_filter steps (Allen + multi_event combined)."""
    return sum(1 for s in sample["steps"] if s["step_kind"] == "temporal_filter")


def _is_tc1(sample: dict) -> bool:
    return _tc_count(sample) == 1


def _temporal_code(sample: dict) -> str | None:
    """Return the single temporal constraint code for a tc=1 sample."""
    for step in sample["steps"]:
        if step["step_kind"] != "temporal_filter":
            continue
        payload = step.get("constraint_payload") or {}
        if "allen_code" in payload:
            return str(payload["allen_code"])
        if "multi_event_type" in payload:
            return str(payload["multi_event_type"])
    return None


def _focus_code_tcgt1(sample: dict) -> str | None:
    """Return the focus Allen code for a tc>1 sample.

    Focus = the first Allen pairwise constraint encountered in the trace.
    Multi-event-only traces are excluded from tc>1 benchmark.
    """
    for step in sample["steps"]:
        if step["step_kind"] != "temporal_filter":
            continue
        payload = step.get("constraint_payload") or {}
        if "allen_code" in payload:
            return str(payload["allen_code"])
    return None  # no pairwise Allen constraint found → skip


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a temporal TKGQG benchmark from trace samples."
    )
    parser.add_argument("--input-jsonl", type=Path, default=TRACE_SAMPLES_PATH)
    parser.add_argument(
        "--mode",
        choices=["tc1", "tc_gt1"],
        default="tc1",
        help="tc1: single-constraint benchmark (default). tc_gt1: multi-constraint benchmark.",
    )
    parser.add_argument(
        "--output-jsonl",
        type=Path,
        default=None,
        help="Output path (default: allen10_benchmark.jsonl for tc1, allen_multi_benchmark.jsonl for tc_gt1)",
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=None,
    )
    parser.add_argument("--per-code", type=int, default=10)
    parser.add_argument("--random-seed", type=int, default=13)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Resolve default output paths based on mode
    if args.output_jsonl is None:
        args.output_jsonl = TEMPORAL_TKGQG_DIR / (
            "allen10_benchmark.jsonl" if args.mode == "tc1"
            else "allen_multi_benchmark.jsonl"
        )
    if args.summary_json is None:
        args.summary_json = TEMPORAL_TKGQG_DIR / (
            "allen10_benchmark_summary.json" if args.mode == "tc1"
            else "allen_multi_benchmark_summary.json"
        )

    raw_samples = load_jsonl(args.input_jsonl)

    # Filter by sampling mode prefix: tc1 traces for tc1 benchmark, any traces for tc_gt1
    mode_prefix = "_tc1_" if args.mode == "tc1" else "_any_"
    raw_samples = [s for s in raw_samples if mode_prefix in str(s.get("sample_id", ""))]
    print(f"after mode filter ({mode_prefix}): {len(raw_samples)} samples")

    if args.mode == "tc1":
        # --- tc=1: single temporal constraint ---
        samples = [s for s in raw_samples if _is_tc1(s)]
        print(f"loaded {len(raw_samples)} samples, {len(samples)} have tc=1")

        buckets: dict[str, list[dict]] = defaultdict(list)
        for sample in samples:
            code = _temporal_code(sample)
            if code:
                buckets[code].append(sample)

        # all known codes: Allen pairwise + multi-event
        all_codes = list(ALLEN_INFO.keys()) + list(MULTI_EVENT_INFO.keys())

    else:
        # --- tc>1: multiple Allen pairwise constraints in the same trace ---
        # Requires ≥2 Allen temporal filters (multi_event_type constraints don't count).
        # This naturally selects sr_chain_hop2/hop3 traces with Allen constraints on both hops.
        samples = [s for s in raw_samples if _allen_tc_count(s) > 1]
        print(f"loaded {len(raw_samples)} samples, {len(samples)} have ≥2 Allen constraints")

        buckets = defaultdict(list)
        for sample in samples:
            code = _focus_code_tcgt1(sample)
            if code:
                buckets[code].append(sample)

        # tc>1 only uses Allen pairwise codes (no multi-event rank in chains)
        all_codes = list(ALLEN_INFO.keys())

    rng = __import__("random").Random(args.random_seed)
    rows: list[dict] = []
    summary: dict = {
        "mode": args.mode,
        "per_code_target": args.per_code,
        "trace_total": len(samples),
        "selected_counts": {},
        "available_counts": {},
    }

    for code in all_codes:
        candidates = list(buckets.get(code, []))
        summary["available_counts"][code] = len(candidates)
        if not candidates:
            summary["selected_counts"][code] = 0
            continue
        # For Allen codes use the existing priority; for multi-event codes
        # the priority still works (it just won't find the allen_code).
        ranked = sorted(
            candidates,
            key=lambda s: sample_priority(s, code, rng),
        )
        selected = ranked[: args.per_code]
        summary["selected_counts"][code] = len(selected)
        for index, sample in enumerate(selected, start=1):
            # build_benchmark_record needs an Allen code present in ALLEN_INFO.
            # For multi-event codes we pass the code and handle gracefully.
            if code in ALLEN_INFO:
                rows.append(build_benchmark_record(sample, code, index))
            else:
                # For multi-event types, craft a record manually
                from tkgqg_shared import (
                    build_agent_question,
                    event_line,
                    sample_answer,
                    temporal_constraints as tc_fn,
                )

                agent = build_agent_question(sample)
                tc_list = tc_fn(sample)
                # find the multi-event step
                focus_step_index = None
                for step in sample["steps"]:
                    if step["step_kind"] == "temporal_filter":
                        payload = step.get("constraint_payload") or {}
                        if payload.get("multi_event_type") == code:
                            focus_step_index = step["step_index"]
                            break
                rows.append(
                    {
                        "benchmark_id": f"{code}_test_{index:02d}",
                        "source_sample_id": sample["sample_id"],
                        "template_id": sample["template_id"],
                        "graph_structure": sample["graph_structure"],
                        "hop_count": sample["hop_count"],
                        "focus_allen_code": code,
                        "focus_allen_name_zh": code,
                        "focus_allen_description_en": MULTI_EVENT_INFO[code],
                        "focus_temporal_step_index": focus_step_index,
                        "seed": sample["seed"],
                        "steps": sample["steps"],
                        "answer": sample_answer(sample),
                        "participating_events": sample["participating_events"],
                        "subgraph_lines": [
                            event_line(event)
                            for event in sample["participating_events"]
                        ],
                        "temporal_constraints": tc_list,
                        "agent_trace": agent["trace"],
                        "gold_question": agent["question"],
                        "original_question_en": agent["question"],
                    }
                )

    write_jsonl(args.output_jsonl, rows)
    write_json(args.summary_json, summary)
    print(f"benchmark written to: {args.output_jsonl}")
    print(f"summary written to: {args.summary_json}")
    print(f"total benchmark items: {len(rows)}")


if __name__ == "__main__":
    main()
