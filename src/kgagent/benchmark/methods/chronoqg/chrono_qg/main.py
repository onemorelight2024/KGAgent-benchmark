#!/usr/bin/env python3
"""
TKGQG Pipeline — End-to-End CLI Entry Point.

Usage:
    # Full pipeline (relation cleaning -> sampling -> benchmark -> verify)
    python main.py run --config config.json

    # Single stage
    python main.py sample   --config config.json
    python main.py build    --config config.json
    python main.py verify   --config config.json

    # Generate a default config file
    python main.py init-config --kg-dir /path/to/kg --granularity day -o config.json

Input: a TKG in TSV format  (subject \\t relation \\t object \\t ts \\t te)
       + entity/relation text mappings
Output: verified QA benchmark (Dataset / Dataset++ JSONL)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Ensure script directory is importable
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tkgqg_config import PipelineConfig  # noqa: E402


# ===================================================================
#  Stage runners — each wraps an existing script's main()
# ===================================================================

def _run_sample(cfg: PipelineConfig) -> Path:
    """Stage 1: Trace sampling from KG."""
    from build_temporal_query_traces import main as _trace_main
    import build_temporal_query_traces as mod

    # Inject config into module-level constants used by the script
    _inject_shared_config(cfg)

    out_jsonl = cfg.output_dir / "traces" / "trace_samples.jsonl"
    manifest  = cfg.output_dir / "traces" / "manifest.json"
    report    = cfg.output_dir / "traces" / "report_examples.md"
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)

    # Derive timestamp digit length and scaled max_time from granularity
    _TS_LEN_MAP = {"year": 4, "month": 6, "day": 8}
    ts_len = _TS_LEN_MAP.get(cfg.time_granularity, 0)
    # Scale max_time to match timestamp format:
    #   year 2025 → 2025,  month → 202512,  day → 20251231
    _MAX_TIME_SCALE = {4: 1, 6: 100, 8: 10000}
    scaled_max = cfg.max_time * _MAX_TIME_SCALE.get(ts_len, 1)

    # Build a synthetic argparse namespace
    args = argparse.Namespace(
        kg_path=cfg.kg_path,
        entity_map_path=cfg.entity_map_path,
        relation_map_path=cfg.relation_map_path,
        output_jsonl=out_jsonl,
        manifest_json=manifest,
        report_md=report,
        min_seed_answers=cfg.min_seed_answers,
        max_seed_answers=cfg.max_seed_answers,
        max_forward_answers=cfg.max_forward_answers,
        max_instances_per_answer=cfg.max_instances_per_answer,
        max_year=scaled_max,
        ts_len=ts_len,
        max_per_template=cfg.max_per_template,
        attempts_per_seed_template=cfg.attempts_per_seed_template,
        random_seed=cfg.random_seed,
    )

    # Override the module's _parse_args so main() picks up our args
    mod._parse_args = lambda: args
    _trace_main()
    print(f"[sample] output → {out_jsonl}")
    return out_jsonl


def _run_build(cfg: PipelineConfig, trace_jsonl: Path | None = None) -> tuple[Path, Path]:
    """Stage 2: Build benchmark JSONL (tc1 + tc_gt1)."""
    import build_tkgqg_benchmark as mod

    _inject_shared_config(cfg)

    if trace_jsonl is None:
        trace_jsonl = cfg.output_dir / "traces" / "trace_samples.jsonl"

    out_tc1   = cfg.output_dir / "benchmark" / "benchmark_tc1.jsonl"
    out_tcgt1 = cfg.output_dir / "benchmark" / "benchmark_tc_gt1.jsonl"
    out_tc1.parent.mkdir(parents=True, exist_ok=True)

    for mode, out_path in [("tc1", out_tc1), ("tc_gt1", out_tcgt1)]:
        summary_path = out_path.parent / out_path.name.replace(".jsonl", "_summary.json")
        args = argparse.Namespace(
            input_jsonl=trace_jsonl,
            output_jsonl=out_path,
            summary_json=summary_path,
            mode=mode,
            per_code=cfg.per_code,
            random_seed=cfg.random_seed,
        )
        mod._parse_args = lambda _a=args: _a
        mod.main()
        print(f"[build] {mode} → {out_path}")

    return out_tc1, out_tcgt1


def _run_verify(cfg: PipelineConfig,
                benchmark_jsonl: Path | None = None,
                tc_mode: str = "tc1") -> Path:
    """Stage 3: Rewrite + verify → Dataset / Dataset++."""
    import eval_benchmark as mod

    _inject_shared_config(cfg)

    if benchmark_jsonl is None:
        benchmark_jsonl = cfg.output_dir / "benchmark" / f"benchmark_{tc_mode}.jsonl"

    out_dir = cfg.output_dir / "verified" / tc_mode
    out_dir.mkdir(parents=True, exist_ok=True)

    args = argparse.Namespace(
        input_jsonl=benchmark_jsonl,
        tc_mode=tc_mode,
        output_dir=out_dir,
        model=cfg.rewrite_model,
        answer_model=cfg.answer_model,
        base_url=cfg.api_base_url,
        api_key=cfg.api_key,
        parallelism=cfg.parallelism,
        save_every=20,
    )
    mod._parse_args = lambda _a=args: _a
    mod.main()
    print(f"[verify] {tc_mode} → {out_dir}")
    return out_dir


# ===================================================================
#  Config injection — propagate granularity into tkgqg_shared
# ===================================================================

def _load_relation_type_from_tsv(tsv_path: Path | None) -> dict[str, str] | None:
    """Load relation type classification from a TSV file.

    Supports two formats:
      - Simple:  rel_id \\t P|I|D
      - Verbose:  same as the CronKGQA hand-curated TSV (category headers)
    Returns None if the file doesn't exist or can't be parsed.
    """
    if tsv_path is None or not Path(tsv_path).exists():
        return None
    result: dict[str, str] = {}
    try:
        lines = Path(tsv_path).read_text(encoding="utf-8").split("\n")
    except Exception:
        return None
    current_type: str | None = None
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        # Category header: =====...\nP — Point ...
        if s.startswith("="):
            cat = s.strip("= ")
            if cat.startswith("P") or "Point" in cat.lower():
                current_type = "P"
            elif cat.startswith("I") or "Interval" in cat.lower():
                current_type = "I"
            elif cat.startswith("D") or "Deleted" in cat.lower() or "U" in cat.lower():
                current_type = "D"
            continue
        parts = s.split("\t")
        if len(parts) < 2:
            continue
        # Simple format: rel_id \t P|I|D
        if parts[1].strip() in ("P", "I", "D"):
            result[parts[0].strip()] = parts[1].strip()
        # Verbose format: rel_id \t name \t ...  under a category header
        elif current_type is not None and parts[0].strip().startswith("P"):
            result[parts[0].strip()] = current_type
    return result if result else None


def _inject_shared_config(cfg: PipelineConfig) -> None:
    """Patch tkgqg_shared module-level dicts with config-driven granularity."""
    import tkgqg_shared as shared

    # Swap Allen description template
    shared.ALLEN_YEAR_DESC = cfg.allen_desc

    # Swap strict Allen definitions
    shared.ALLEN_STRICT_DEF = cfg.allen_strict_def

    # Swap deleted relations (in-place mutation so modules that already imported
    # DELETED_RELATIONS by reference see the update)
    if cfg.deleted_relations:
        shared.DELETED_RELATIONS.clear()
        shared.DELETED_RELATIONS.update(cfg.deleted_relations)

    # Load relation type classification from config's TSV (or fall back to hardcoded)
    _rt_from_tsv = _load_relation_type_from_tsv(cfg.relation_type_tsv)
    if _rt_from_tsv:
        shared.RELATION_TEMPORAL_TYPE = _rt_from_tsv

    # Patch effective_time_range to use config-aware expansion
    _expansion = cfg.point_expansion
    _orig_rel_type = shared.RELATION_TEMPORAL_TYPE

    def _patched_effective_time_range(relation: str, ts: int, te: int) -> tuple[int, int]:
        rtype = _orig_rel_type.get(relation)
        if rtype == "P":
            return (ts, ts)
        if ts == te:
            return (ts, ts + _expansion)
        return (ts, te)

    shared.effective_time_range = _patched_effective_time_range

    # Inject prompt timestamp preamble for scripts that build prompts
    try:
        import prompts
        # Patch STAGE1_ANSWER_PROMPT's timestamp line
        old_preamble = "All timestamps are integer years."
        new_preamble = cfg.timestamp_preamble
        if old_preamble in prompts.STAGE1_ANSWER_PROMPT:
            prompts.STAGE1_ANSWER_PROMPT = prompts.STAGE1_ANSWER_PROMPT.replace(
                old_preamble, new_preamble)
        if old_preamble in prompts.VERIFY_ANSWER_PROMPT:
            prompts.VERIFY_ANSWER_PROMPT = prompts.VERIFY_ANSWER_PROMPT.replace(
                old_preamble, new_preamble)
        # Also patch failure diagnosis
        old_facts_line = "Subgraph facts (with year-level timestamps):"
        new_facts_line = f"Subgraph facts (with {cfg.time_granularity}-level timestamps):"
        if hasattr(prompts, "FAILURE_DIAGNOSIS_PROMPT"):
            prompts.FAILURE_DIAGNOSIS_PROMPT = prompts.FAILURE_DIAGNOSIS_PROMPT.replace(
                old_facts_line, new_facts_line)
    except ImportError:
        pass


# ===================================================================
#  Full pipeline
# ===================================================================

def _run_analyze_relations(
    cfg: PipelineConfig,
    point_threshold: float = 0.8,
    interval_threshold: float = 0.35,
    min_facts: int = 5,
    output_tsv: Path | None = None,
) -> Path:
    """Auto-classify relations as Point (P) / Interval (I) / Deleted (D).

    Heuristic:
      - ts==te ratio > point_threshold → Point
      - ts==te ratio < interval_threshold → Interval (ts!=te dominant)
      - in between (ambiguous) → Deleted
      - total facts < min_facts → Deleted (too few to classify reliably)
    """
    from collections import defaultdict
    import csv

    if output_tsv is None:
        output_tsv = cfg.output_dir / "relation_type.tsv"

    stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "point": 0})
    print(f"Scanning {cfg.kg_path} ...")
    with open(cfg.kg_path) as f:
        for i, line in enumerate(f):
            parts = line.strip().split("\t")
            if len(parts) < 5:
                continue
            r, ts, te = parts[1], parts[3], parts[4]
            stats[r]["total"] += 1
            if ts == te:
                stats[r]["point"] += 1
            if (i + 1) % 1_000_000 == 0:
                print(f"  {i+1:,} lines", end="\r", flush=True)
    print(f"\n  Scanned {sum(s['total'] for s in stats.values()):,} facts across "
          f"{len(stats)} relations")

    # Load relation labels for readable output
    rel_labels: dict[str, str] = {}
    if Path(cfg.relation_map_path).exists():
        with open(cfg.relation_map_path) as f:
            for line in f:
                parts = line.strip().split("\t", 1)
                if len(parts) == 2:
                    rel_labels[parts[0]] = parts[1]

    # Classify and write
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    counts = {"P": 0, "I": 0, "D": 0}
    rows = []
    for r in sorted(stats.keys()):
        total = stats[r]["total"]
        pct = stats[r]["point"] / total
        if total < min_facts:
            typ = "D"
        elif pct > point_threshold:
            typ = "P"
        elif pct < interval_threshold:
            typ = "I"
        else:
            typ = "D"  # ambiguous middle ground
        counts[typ] += 1
        label = rel_labels.get(r, "")
        rows.append((r, typ, label, total, f"{pct:.1%}"))

    with open(output_tsv, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["relation_id", "type", "label", "total_facts", "point_ratio"])
        for row in rows:
            w.writerow(row)

    print(f"Done. P={counts['P']}, I={counts['I']}, D={counts['D']} → {output_tsv}")
    # Also update config
    cfg.update_deleted([r[0] for r in rows if r[1] == "D"])
    return output_tsv


def run_full_pipeline(cfg: PipelineConfig) -> None:
    """Run the released construction stages sequentially."""
    t0 = time.time()
    print("=" * 60)
    print("TKGQG Pipeline — End-to-End Run")
    print(f"  KG:          {cfg.kg_path}")
    print(f"  Granularity: {cfg.time_granularity}")
    print(f"  Output:      {cfg.output_dir}")
    print("=" * 60)

    # Save config for reproducibility
    cfg.save(cfg.output_dir / "pipeline_config.json")

    # Stage 1: Sample traces
    print("\n[1/3] Sampling temporal query traces...")
    trace_jsonl = _run_sample(cfg)

    # Stage 2: Build benchmark
    print("\n[2/3] Building benchmark...")
    tc1, tcgt1 = _run_build(cfg, trace_jsonl)

    # Stage 3: Verify (both tc1 and tc_gt1)
    print("\n[3/3] Verifying (rewrite + answer)...")
    _run_verify(cfg, tc1, "tc1")
    _run_verify(cfg, tcgt1, "tc_gt1")

    elapsed = time.time() - t0
    print(f"\nPipeline complete in {elapsed:.0f}s.")
    print(f"Results in: {cfg.output_dir}")


# ===================================================================
#  CLI
# ===================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tkgqg",
        description="TKGQG: Temporal Knowledge Graph Question Generation Pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── init-config ─────────────────────────────────────────────────
    p_init = sub.add_parser("init-config",
                            help="Generate a default config JSON file")
    p_init.add_argument("--kg-dir", type=Path, required=True,
                        help="Directory containing full.txt, entity map, relation map")
    p_init.add_argument("--granularity", type=str, default="year",
                        choices=["year", "month", "day"])
    p_init.add_argument("--relation-type-tsv", type=Path, default=None,
                        help="P/I/D classification TSV (optional)")
    p_init.add_argument("-o", "--output", type=Path, default=Path("config.json"))

    # ── analyze-relations ─────────────────────────────────────────────
    p_an = sub.add_parser("analyze-relations",
                           help="Auto-classify relations as Point/Interval/Deleted")
    p_an.add_argument("--config", type=Path, required=True,
                      help="Pipeline config JSON")
    p_an.add_argument("--point-threshold", type=float, default=0.8,
                      help="ts==te ratio above which → Point (default: 0.8)")
    p_an.add_argument("--interval-threshold", type=float, default=0.35,
                      help="ts==te ratio below which → Interval (default: 0.35)")
    p_an.add_argument("--min-facts", type=int, default=5,
                      help="Minimum facts to keep (default: 5)")
    p_an.add_argument("-o", "--output", type=Path, default=None,
                      help="Output TSV path (default: {output_dir}/relation_type.tsv)")

    # ── run (full pipeline) ─────────────────────────────────────────
    p_run = sub.add_parser("run", help="Run full pipeline end-to-end")
    p_run.add_argument("--config", type=Path, required=True)

    # ── sample ──────────────────────────────────────────────────────
    p_samp = sub.add_parser("sample", help="Stage 1: trace sampling")
    p_samp.add_argument("--config", type=Path, required=True)

    # ── build ───────────────────────────────────────────────────────
    p_build = sub.add_parser("build", help="Stage 2: build benchmark JSONL")
    p_build.add_argument("--config", type=Path, required=True)
    p_build.add_argument("--trace-jsonl", type=Path, default=None)

    # ── verify ──────────────────────────────────────────────────────
    p_ver = sub.add_parser("verify", help="Stage 3: rewrite + verify")
    p_ver.add_argument("--config", type=Path, required=True)
    p_ver.add_argument("--benchmark-jsonl", type=Path, default=None)
    p_ver.add_argument("--tc-mode", choices=["tc1", "tc_gt1"], default="tc1")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "init-config":
        kg_dir = args.kg_dir.resolve()
        cfg = PipelineConfig(
            kg_path=kg_dir / "full.txt",
            entity_map_path=kg_dir / "wd_id2entity_text.txt",
            relation_map_path=kg_dir / "wd_id2relation_text.txt",
            relation_type_tsv=args.relation_type_tsv,
            output_dir=Path("output"),
            time_granularity=args.granularity,
        )
        cfg.save(args.output)
        print(f"Config written to {args.output}")
        print("  Edit the file to adjust models, parallelism, deleted_relations, etc.")
        return

    cfg = PipelineConfig.load(args.config)

    if args.command == "run":
        run_full_pipeline(cfg)
    elif args.command == "sample":
        _run_sample(cfg)
    elif args.command == "build":
        _run_build(cfg, trace_jsonl=args.trace_jsonl)
    elif args.command == "verify":
        _run_verify(cfg, benchmark_jsonl=args.benchmark_jsonl,
                    tc_mode=args.tc_mode)
    elif args.command == "analyze-relations":
        _run_analyze_relations(
            cfg,
            point_threshold=args.point_threshold,
            interval_threshold=args.interval_threshold,
            min_facts=args.min_facts,
            output_tsv=args.output,
        )


if __name__ == "__main__":
    main()
