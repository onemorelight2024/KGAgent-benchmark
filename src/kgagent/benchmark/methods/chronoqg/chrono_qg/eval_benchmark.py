"""Unified rewrite + verification pipeline for Dataset / Dataset++.

Flow per record:
  1. Rewrite: LLM rewrites template question to natural English
  2. GPT-5 answers rewritten question + fuzzy match
     → PASS: Dataset (tc1 mode) or Dataset++ (tc_gt1 mode)
  3. Verifier: strict Allen constraint check on gold answer
     → PASS: Dataset++ (tc1 mode) or discard (tc_gt1 mode)
  4. Rewrite fix + retry once (same routing)
     → still FAIL: discard

Usage:
    python eval_benchmark.py \\
        --input-jsonl allen10_benchmark.jsonl \\
        --tc-mode tc1 \\
        --model gpt-5.4 \\
        --answer-model gpt-5 \\
        --parallelism 16
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tkgqg_shared import (
    ALLEN_INFO,
    ALLEN_STRICT_DEF,
    TEMPORAL_TKGQG_DIR,
    load_jsonl,
    write_json,
    write_jsonl,
)
from prompts import (
    REWRITE_PROMPT,
    REWRITE_FIX_PROMPT,
    STAGE1_ANSWER_PROMPT,
    VERIFY_ANSWER_PROMPT,
    VERIFY_JUDGE_PROMPT,
)
from verify_tkgqg_gold_benchmark import (
    _build_cot_scaffold,
    _clean_text,
    _parse_json,
)
from llm_client import call_chat_completions_with_usage


# ---------------------------------------------------------------------------
#  Token-tracked LLM call
# ---------------------------------------------------------------------------

import threading

_usage_lock = threading.Lock()
_usage_totals: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "reasoning_tokens": 0,
    "total_tokens": 0,
    "call_count": 0,
}


def _llm_tracked(
    prompt: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    temperature: float = 0.0,
    max_tokens: int = 256,
) -> str:
    """Call LLM and accumulate token usage globally."""
    result = call_chat_completions_with_usage(
        prompt, model,
        base_url=base_url, api_key=api_key,
        temperature=temperature, max_tokens=max_tokens,
        timeout=180.0,
    )
    usage = result["usage"]
    with _usage_lock:
        for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"):
            _usage_totals[k] += usage.get(k, 0)
        _usage_totals["call_count"] += 1
    return result["content"]


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _answer_and_judge(
    question: str,
    gold_answer: str,
    facts: str,
    cot_scaffold: str,
    answer_model: str,
    judge_model: str,
    base_url: str | None,
    api_key: str | None,
) -> dict:
    """Ask LLM to answer, then judge equivalence."""
    answer_raw = _llm_tracked(
        STAGE1_ANSWER_PROMPT.format(
            question=question, facts=facts, cot_scaffold=cot_scaffold,
        ),
        model=answer_model, base_url=base_url, api_key=api_key,
        temperature=0.0, max_tokens=5000,
    )
    answer_json: dict = {}
    try:
        answer_json = _parse_json(answer_raw)
        predicted = _clean_text(str(answer_json.get("final_answer", "")))
        if not predicted:
            raise ValueError("empty")
    except Exception:
        predicted = _clean_text(answer_raw)

    judge_raw = _llm_tracked(
        VERIFY_JUDGE_PROMPT.format(
            gold_answer=gold_answer, pred_answer=predicted,
            question=question, facts=facts,
        ),
        model=judge_model, base_url=base_url, api_key=api_key,
        temperature=0.0, max_tokens=300,
    )
    try:
        judge = _parse_json(judge_raw)
        equivalent = int(judge.get("equivalent", 0))
    except Exception:
        judge = {"equivalent": 0, "reason": f"parse_error: {judge_raw[:200]}"}
        equivalent = 0

    return {
        "predicted": predicted,
        "equivalent": equivalent,
        "answer_raw": answer_raw,
        "judge_raw": judge_raw,
        "judge": judge,
    }


def _verify_gold(
    question: str,
    gold_answer: str,
    facts: str,
    code: str,
    base_url: str | None,
    api_key: str | None,
    model: str,
) -> dict:
    """Run strict Allen constraint verification on gold answer."""
    allen_natural = ALLEN_INFO.get(code, {}).get("en", code)
    allen_strict = ALLEN_STRICT_DEF.get(code, "No strict definition available.")
    scaffold = "Verify each constraint mentioned in the question against the subgraph facts."

    verify_raw = _llm_tracked(
        VERIFY_ANSWER_PROMPT.format(
            allen_code=code,
            allen_natural=allen_natural,
            allen_strict_def=allen_strict,
            question=question,
            proposed_answer=gold_answer,
            facts=facts,
            cot_scaffold=scaffold,
        ),
        model=model, base_url=base_url, api_key=api_key,
        temperature=0.0, max_tokens=5000,
    )
    try:
        verify_json = _parse_json(verify_raw)
        verdict = verify_json.get("verdict", "fail")
        passed = verdict == "pass" and verify_json.get("all_satisfied", False)
        reason = verify_json.get("reason", "")
    except Exception:
        passed = False
        reason = f"parse_error: {verify_raw[:200]}"
        verify_json = {}

    return {
        "verifier_pass": passed,
        "verifier_reason": reason,
        "verifier_raw": verify_raw,
        "verifier_json": verify_json,
    }


def _route(gpt5_pass: bool, verifier_pass: bool | None, tc_mode: str) -> str:
    """Decide where this record goes: dataset / dataset_pp / discard."""
    if gpt5_pass:
        return "dataset" if tc_mode == "tc1" else "dataset_pp"
    if verifier_pass:
        return "dataset_pp" if tc_mode == "tc1" else "discard"
    return "discard"


def _event_to_subgraph_fact(event: dict) -> dict:
    return {
        "fid": event.get("fid"),
        "subject_id": event.get("s", ""),
        "subject": event.get("s_text", ""),
        "relation_id": event.get("r", ""),
        "relation": event.get("r_text", ""),
        "object_id": event.get("o", ""),
        "object": event.get("o_text", ""),
        "start": event.get("ts"),
        "end": event.get("te"),
        "time_type": event.get("time_type", ""),
    }


def _step_to_search_process(step: dict) -> dict:
    return {
        "step_index": step.get("step_index"),
        "operation": step.get("step_kind", ""),
        "description": step.get("current_question_stub", ""),
        "candidate_count": step.get("answer_count"),
        "support_fids": step.get("support_fids", []),
    }


def _temporal_constraint_to_release(record: dict, constraint: dict) -> dict:
    step_index = constraint.get("step_index")
    step = next(
        (s for s in record.get("steps", []) if s.get("step_index") == step_index),
        {},
    )
    return {
        "type": constraint.get("type", ""),
        "code": constraint.get("code", ""),
        "description": constraint.get(
            "description",
            constraint.get("description_en", ""),
        ),
        "step_index": step_index,
        "name_zh": constraint.get("name_zh", ""),
        "support_fids": step.get("support_fids", []),
        "binding": step.get("constraint_payload", {}),
    }


def _to_release_record(record: dict, result: dict) -> dict:
    """Convert a verified benchmark record to the public release schema."""
    constraints = [
        _temporal_constraint_to_release(record, c)
        for c in record.get("temporal_constraints", [])
    ]
    answer = record.get("answer", {})
    if isinstance(answer, dict):
        answer_text = answer.get("text", "")
        answer_id = answer.get("id", "")
    else:
        answer_text = str(answer)
        answer_id = ""

    metadata = {
        "template_id": record.get("template_id", ""),
        "source_sample_id": record.get("source_sample_id", ""),
        "answer_id": answer_id,
        "routing": result.get("routing", "discard"),
        "verified_pass": result.get("routing") != "discard",
        "focus_allen_code": record.get("focus_allen_code", ""),
        "focus_temporal_step_index": record.get("focus_temporal_step_index"),
        "retry_used": result.get("retry_used", False),
        "gpt5_pass": result.get("gpt5_pass"),
        "verifier_pass": result.get("verifier_pass"),
        "seed": record.get("seed", {}),
        "agent_trace": record.get("agent_trace", []),
    }
    if "gpt5_judge" in result:
        metadata["gpt5_judge"] = result["gpt5_judge"]
    if "verifier_reason" in result:
        metadata["verifier_reason"] = result["verifier_reason"]

    return {
        "id": record.get("benchmark_id", result.get("benchmark_id", "")),
        "graph_structure": record.get("graph_structure", ""),
        "hop_count": record.get("hop_count", 0),
        "subgraph": [
            _event_to_subgraph_fact(e)
            for e in record.get("participating_events", [])
        ],
        "space_search_process": [
            _step_to_search_process(s)
            for s in record.get("steps", [])
        ],
        "temporal_constraints": constraints,
        "temporal_constraint_count": len(constraints),
        "raw_question": result.get(
            "original_question_en",
            record.get("original_question_en", record.get("gold_question", "")),
        ),
        "final_question": result.get(
            "gold_question",
            result.get("fixed_question_en", record.get("gold_question", "")),
        ),
        "answer": answer_text,
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
#  Core per-record processing
# ---------------------------------------------------------------------------


def _process_one(
    index: int,
    record: dict,
    tc_mode: str,
    answer_model: str,
    judge_model: str,
    base_url: str | None,
    api_key: str | None,
) -> tuple[int, dict]:
    """Full pipeline for a single benchmark record."""
    facts = "\n".join(f"- {line}" for line in record["subgraph_lines"])
    gold_answer = record["answer"]["text"]
    original_question = record.get("original_question_en") or record.get("gold_question", "")
    code = record["focus_allen_code"]
    cot_scaffold = _build_cot_scaffold(record)

    result: dict = {
        "benchmark_id": record["benchmark_id"],
        "focus_allen_code": code,
        "template_id": record["template_id"],
        "original_question_en": original_question,
    }

    # ---- Phase 1: Rewrite ----
    rewrite_raw = _llm_tracked(
        REWRITE_PROMPT.format(
            original_question=original_question,
            facts=facts,
            target_answer=gold_answer,
        ),
        model=judge_model, base_url=base_url, api_key=api_key,
        temperature=0.7,
    )
    rewritten = _clean_text(rewrite_raw)
    result["gold_question"] = rewritten

    # ---- Phase 2: GPT-5 answers rewritten question ----
    ans = _answer_and_judge(
        rewritten, gold_answer, facts, cot_scaffold,
        answer_model, judge_model, base_url, api_key,
    )
    result["gpt5_predicted"] = ans["predicted"]
    result["gpt5_pass"] = ans["equivalent"] == 1
    result["gpt5_judge"] = ans["judge"]

    routing = _route(result["gpt5_pass"], None, tc_mode)
    if routing != "discard":
        result["routing"] = routing
        result["retry_used"] = False
        result["verifier_pass"] = None
        return index, result

    # ---- Phase 3: Verifier ----
    ver = _verify_gold(rewritten, gold_answer, facts, code, base_url, api_key, judge_model)
    result["verifier_pass"] = ver["verifier_pass"]
    result["verifier_reason"] = ver["verifier_reason"]
    result["verifier_raw"] = ver["verifier_raw"]

    routing = _route(False, ver["verifier_pass"], tc_mode)
    if routing != "discard":
        result["routing"] = routing
        result["retry_used"] = False
        return index, result

    # ---- Phase 4: Rewrite fix + retry once ----
    fix_raw = _llm_tracked(
        REWRITE_FIX_PROMPT.format(
            original_question=original_question,
            failed_rewrite=rewritten,
            wrong_answer=ans["predicted"],
            target_answer=gold_answer,
            facts=facts,
        ),
        model=judge_model, base_url=base_url, api_key=api_key,
        temperature=0.7,
    )
    fixed = _clean_text(fix_raw)
    result["fixed_question_en"] = fixed
    result["fix_raw"] = fix_raw

    # ---- Phase 5: Re-verify fixed question ----
    ans2 = _answer_and_judge(
        fixed, gold_answer, facts, cot_scaffold,
        answer_model, judge_model, base_url, api_key,
    )
    result["retry_gpt5_predicted"] = ans2["predicted"]
    result["retry_gpt5_pass"] = ans2["equivalent"] == 1

    routing = _route(ans2["equivalent"] == 1, None, tc_mode)
    if routing != "discard":
        result["gold_question"] = fixed  # use fixed version
        result["routing"] = routing
        result["retry_used"] = True
        return index, result

    # Retry verifier
    ver2 = _verify_gold(fixed, gold_answer, facts, code, base_url, api_key, judge_model)
    result["retry_verifier_pass"] = ver2["verifier_pass"]
    result["retry_verifier_reason"] = ver2["verifier_reason"]

    routing = _route(False, ver2["verifier_pass"], tc_mode)
    if routing != "discard":
        result["gold_question"] = fixed
        result["routing"] = routing
        result["retry_used"] = True
        return index, result

    result["routing"] = "discard"
    result["retry_used"] = True
    return index, result


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified rewrite + verify pipeline.")
    parser.add_argument("--input-jsonl", type=Path, required=True)
    parser.add_argument("--tc-mode", choices=["tc1", "tc_gt1"], required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--model", default="gpt-5.4", help="Rewrite/judge model")
    parser.add_argument("--answer-model", default="gpt-5", help="Strong answer model")
    parser.add_argument("--base-url", default=None, help="Legacy compatibility option; Claude SDK routing uses ANTHROPIC_* / CCR")
    parser.add_argument("--api-key", default=None, help="Legacy compatibility option; Claude SDK routing uses ANTHROPIC_* / CCR")
    parser.add_argument("--parallelism", type=int, default=64)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.output_dir is None:
        args.output_dir = TEMPORAL_TKGQG_DIR / f"eval_{args.tc_mode}"
    args.output_dir.mkdir(parents=True, exist_ok=True)

    records = load_jsonl(args.input_jsonl)
    print(f"loaded {len(records)} records, tc_mode={args.tc_mode}")

    results: list[dict] = [{}] * len(records)
    done = 0

    with ThreadPoolExecutor(max_workers=args.parallelism) as pool:
        futures = {
            pool.submit(
                _process_one, i, rec, args.tc_mode,
                args.answer_model, args.model, args.base_url, args.api_key,
            ): i
            for i, rec in enumerate(records)
        }
        for future in as_completed(futures):
            idx, result = future.result()
            results[idx] = result
            done += 1
            if done % 10 == 0 or done == len(records):
                print(f"  [{done}/{len(records)}] {result['benchmark_id']}: {result['routing']}")

    # ---- Split by routing ----
    dataset: list[dict] = []
    dataset_pp: list[dict] = []
    discarded: list[dict] = []

    for rec, r in zip(records, results):
        release_record = _to_release_record(rec, r)
        routing = r.get("routing", "discard")
        if routing == "dataset":
            dataset.append(release_record)
        elif routing == "dataset_pp":
            dataset_pp.append(release_record)
        else:
            discarded.append(release_record)

    # ---- Write outputs ----
    write_jsonl(args.output_dir / "dataset.jsonl", dataset)
    write_jsonl(args.output_dir / "dataset_pp.jsonl", dataset_pp)
    write_jsonl(args.output_dir / "discarded.jsonl", discarded)
    write_jsonl(args.output_dir / "all_details.jsonl", results)

    # ---- Summary ----
    by_code = defaultdict(lambda: Counter())
    for r in results:
        by_code[r["focus_allen_code"]][r["routing"]] += 1

    summary = {
        "tc_mode": args.tc_mode,
        "total": len(results),
        "dataset": len(dataset),
        "dataset_pp": len(dataset_pp),
        "discarded": len(discarded),
        "retry_used": sum(1 for r in results if r.get("retry_used")),
        "token_usage": dict(_usage_totals),
        "by_code": {
            code: dict(counts) for code, counts in sorted(by_code.items())
        },
    }
    write_json(args.output_dir / "summary.json", summary)

    print(f"\n=== Summary ({args.tc_mode}) ===")
    print(f"  Total:      {summary['total']}")
    print(f"  Dataset:    {summary['dataset']}")
    print(f"  Dataset++:  {summary['dataset_pp']}")
    print(f"  Discarded:  {summary['discarded']}")
    print(f"  Retry used: {summary['retry_used']}")
    tu = summary["token_usage"]
    print("\n=== Token Usage ===")
    print(f"  LLM calls:        {tu['call_count']}")
    print(f"  Prompt tokens:    {tu['prompt_tokens']:,}")
    print(f"  Completion tokens: {tu['completion_tokens']:,}")
    print(f"  Reasoning tokens:  {tu['reasoning_tokens']:,}")
    print(f"  Total tokens:      {tu['total_tokens']:,}")
    print(f"\nOutputs in: {args.output_dir}")


if __name__ == "__main__":
    main()
