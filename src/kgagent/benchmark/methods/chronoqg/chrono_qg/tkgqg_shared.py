from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

TEMPORAL_TKGQG_DIR = Path("output")
TRACE_SAMPLES_PATH = TEMPORAL_TKGQG_DIR / "traces" / "trace_samples.jsonl"

ALLEN_INFO = {
    "tr-1": {"zh": "区间先于区间", "en": "interval X is entirely before interval Y"},
    "tr-2": {"zh": "区间首尾相接", "en": "interval X ends exactly when interval Y starts"},
    "tr-3": {"zh": "区间前交叠", "en": "interval X starts earlier and overlaps the front part of interval Y"},
    "tr-4": {"zh": "同终点前包", "en": "interval X starts earlier but ends together with interval Y"},
    "tr-5": {"zh": "区间包含", "en": "interval X completely contains interval Y"},
    "tr-6": {"zh": "同起点先结束", "en": "interval X and interval Y start together, but X ends earlier"},
    "tr-7": {"zh": "区间全等", "en": "interval X and interval Y have exactly the same span"},
    "tr-8": {"zh": "同起点后包", "en": "interval X and interval Y start together, but X ends later"},
    "tr-9": {"zh": "区间被包含", "en": "interval X is fully contained in interval Y"},
    "tr-10": {"zh": "同终点后起", "en": "interval X starts later but ends together with interval Y"},
    "tr-11": {"zh": "区间后交叠", "en": "interval X starts later and overlaps the back part of interval Y"},
    "tr-12": {"zh": "区间被紧接", "en": "interval X starts exactly when interval Y ends"},
    "tr-13": {"zh": "区间后于区间", "en": "interval X is entirely after interval Y"},
    "tptr-14": {"zh": "点早于区间", "en": "point X is before interval Y"},
    "tptr-15": {"zh": "点卡在区间起点", "en": "point X is exactly at the start of interval Y"},
    "tptr-16": {"zh": "点落在区间内部", "en": "point X falls strictly inside interval Y"},
    "tptr-17": {"zh": "点卡在区间终点", "en": "point X is exactly at the end of interval Y"},
    "tptr-18": {"zh": "点晚于区间", "en": "point X is after interval Y"},
    "trtp-19": {"zh": "区间早于点", "en": "interval X is before point Y"},
    "trtp-20": {"zh": "区间终止于点", "en": "interval X ends exactly at point Y"},
    "trtp-21": {"zh": "区间覆盖点", "en": "interval X contains point Y"},
    "trtp-22": {"zh": "区间起始于点", "en": "interval X starts exactly at point Y"},
    "trtp-23": {"zh": "区间晚于点", "en": "interval X is after point Y"},
    "tp-24": {"zh": "点早于点", "en": "point X is before point Y"},
    "tp-25": {"zh": "点同时点", "en": "point X is the same time as point Y"},
    "tp-26": {"zh": "点晚于点", "en": "point X is after point Y"},
}

# Year-level Allen descriptions.
# Each value is a *template* with {A} = first event label, {B} = second event label.
# This avoids vague pronouns like "the former" / "the latter".
ALLEN_YEAR_DESC = {
    # interval-interval (tr-1 ~ tr-13)
    "tr-1":  "the {A} period ended before the {B} period began",
    "tr-2":  "the {A} period ended in the same year the {B} period began",
    "tr-3":  "the {A} period started earlier and overlapped with the {B} period for some years",
    "tr-4":  "the {A} period started earlier but ended in the same year as the {B} period",
    "tr-5":  "the {A} period fully encompassed the {B} period",
    "tr-6":  "the {A} period started in the same year as the {B} period but ended earlier",
    "tr-7":  "the {A} period spanned exactly the same years as the {B} period",
    "tr-8":  "the {A} period started in the same year as the {B} period but lasted longer",
    "tr-9":  "the {A} period fell entirely within the {B} period",
    "tr-10": "the {A} period started later but ended in the same year as the {B} period",
    "tr-11": "the {A} period started later and overlapped with the tail end of the {B} period",
    "tr-12": "the {A} period began in the same year the {B} period ended",
    "tr-13": "the {A} period began after the {B} period had ended",
    # point-interval (tptr-14 ~ tptr-18)
    "tptr-14": "the {A} event occurred before the {B} period began",
    "tptr-15": "the {A} event occurred in the same year the {B} period began",
    "tptr-16": "the {A} event occurred during the {B} period",
    "tptr-17": "the {A} event occurred in the same year the {B} period ended",
    "tptr-18": "the {A} event occurred after the {B} period ended",
    # interval-point (trtp-19 ~ trtp-23)
    "trtp-19": "the {A} period ended before the {B} event",
    "trtp-20": "the {A} period ended in the same year as the {B} event",
    "trtp-21": "the {A} period was ongoing during the year of the {B} event",
    "trtp-22": "the {A} period began in the same year as the {B} event",
    "trtp-23": "the {A} period began after the {B} event",
    # point-point (tp-24 ~ tp-26)
    "tp-24": "the {A} event happened in an earlier year than the {B} event",
    "tp-25": "the {A} event happened in the same year as the {B} event",
    "tp-26": "the {A} event happened in a later year than the {B} event",
}

# Strict Allen boundary conditions for year-level data.
# These are passed verbatim into verification prompts so the model knows
# exactly how to handle boundary cases (equal start/end years).
# Format: "plain English condition + boundary note"
ALLEN_STRICT_DEF: dict[str, str] = {
    "tr-1":  "te(A) < ts(B)  — A ends strictly before B starts; no touching allowed.",
    "tr-2":  "te(A) == ts(B) — A ends in the exact same year B starts.",
    "tr-3":  "ts(A) < ts(B) AND ts(B) < te(A) AND te(A) < te(B)  — A starts first, they overlap, B outlasts A.",
    "tr-4":  "ts(A) < ts(B) AND te(A) == te(B) — A starts earlier, both end in the same year.",
    "tr-5":  "ts(A) < ts(B) AND te(B) < te(A) — A strictly contains B on both sides.",
    "tr-6":  "ts(A) == ts(B) AND te(A) < te(B) — same start year, A ends earlier.",
    "tr-7":  "ts(A) == ts(B) AND te(A) == te(B) — identical start and end years.",
    "tr-8":  "ts(A) == ts(B) AND te(A) > te(B) — same start year, A ends later.",
    "tr-9":  "ts(B) < ts(A) AND te(A) < te(B) — A is strictly inside B; equal start OR equal end means tr-6/tr-10, NOT tr-9.",
    "tr-10": "ts(A) > ts(B) AND te(A) == te(B) — A starts later, both end in the same year.",
    "tr-11": "ts(A) > ts(B) AND ts(A) < te(B) AND te(A) > te(B) — A starts later but overlaps B's tail.",
    "tr-12": "ts(A) == te(B) — A starts in the exact same year B ends.",
    "tr-13": "ts(A) > te(B) — A starts strictly after B ends; no touching allowed.",
    "tptr-14": "ts(A) < ts(B) — point A is in a year strictly before B's start.",
    "tptr-15": "ts(A) == ts(B) — point A is in the same year B starts.",
    "tptr-16": "ts(B) < ts(A) AND ts(A) < te(B) — point A is strictly inside B; equal to start or end means tptr-15/tptr-17.",
    "tptr-17": "ts(A) == te(B) — point A is in the same year B ends.",
    "tptr-18": "ts(A) > te(B) — point A is in a year strictly after B ends.",
    "trtp-19": "te(A) < ts(B) — A ends strictly before point B.",
    "trtp-20": "te(A) == ts(B) — A ends in the same year as point B.",
    "trtp-21": "ts(A) < ts(B) AND ts(B) < te(A) — point B is strictly inside A; equal to start or end means trtp-22/trtp-20.",
    "trtp-22": "ts(A) == ts(B) — A starts in the same year as point B.",
    "trtp-23": "ts(A) > ts(B) — A starts strictly after point B.",
    "tp-24":  "ts(A) < ts(B) — A happened in a strictly earlier year than B.",
    "tp-25":  "ts(A) == ts(B) — A and B happened in the same year.",
    "tp-26":  "ts(A) > ts(B) — A happened in a strictly later year than B.",
    # multi-event
    "start_rank":    "Rank entities by their start year (ts) in ascending order.",
    "end_rank":      "Rank entities by their end year (te) in ascending order.",
    "duration_rank": "Rank entities by duration (te - ts) in ascending order.",
}

# ---------------------------------------------------------------------------
#  Relation temporal type classification (loaded from TSV at import time)
# ---------------------------------------------------------------------------

def _load_relation_temporal_type() -> dict[str, str]:
    """Return {relation_id: 'P' | 'I' | 'D'} from a hand-curated TSV.

    Returns an empty dict if no TSV is found. The main pipeline injects the
    correct classification at runtime via PipelineConfig.relation_type_tsv.
    """
    tsv = Path("relation_temporal_type_classification.tsv")
    if not tsv.exists():
        return {}
    result: dict[str, str] = {}
    current_type: str | None = None
    for line in tsv.read_text(encoding="utf-8").split("\n"):
        s = line.strip()
        if "P — Point" in s or s.startswith("P —"):
            current_type = "P"
            continue
        if "I — Interval" in s or s.startswith("I —"):
            current_type = "I"
            continue
        if "D —" in s or "U —" in s:
            current_type = "D"
            continue
        if not s or s.startswith("#") or s.startswith("="):
            continue
        parts = s.split("\t")
        if len(parts) < 2 or not parts[0].strip().startswith("P"):
            continue
        rid = parts[0].strip()
        if current_type is not None:
            result[rid] = current_type
    return result


RELATION_TEMPORAL_TYPE: dict[str, str] = _load_relation_temporal_type()


def effective_time_range(relation: str, ts: int, te: int) -> tuple[int, int]:
    """Return the time range to use for Allen computation.

    P relations are treated as instantaneous (point): [ts, ts].
    I relations keep their full span: [ts, te].
      Special case: I relations where ts==te in the data represent a
      1-year tenure — we treat them as [ts, ts+1] so the 6-tuple sign
      (s-e = -1) identifies them as intervals, not points.
    D / unknown relations default to [ts, te] (same special case applied).
    """
    rtype = RELATION_TEMPORAL_TYPE.get(relation)
    if rtype == "P":
        return (ts, ts)
    # Interval (or unknown): ensure ts < te so Allen treats it as an interval
    if ts == te:
        return (ts, ts + 1)
    return (ts, te)


# Relations excluded from the benchmark due to ambiguous temporal semantics.
# These 23 relations cannot be uniformly classified as Point or Interval.
DELETED_RELATIONS: set[str] = {
    "P793",   # significant event — mixed point/interval object types
    "P527",   # has part
    "P361",   # part of
    "P195",   # collection
    "P607",   # conflict
    "P276",   # location
    "P1416",  # affiliation
    "P366",   # use
    "P5096",  # member of the crew of
    "P1029",  # crew member
    "P119",   # place of burial
    "P190",   # twinned administrative body
    "P1347",  # military casualty classification
    "P449",   # original network
    "P3919",  # contributed to creative work
    "P3342",  # significant person
    "P1830",  # owner of
    "P1336",  # territory claimed by
    "P170",   # creator
    "P2758",  # CNC film rating (France)
    "P176",   # manufacturer
    "P737",   # influenced by
    "P541",   # office contested
    "P272",   # production company
}

MULTI_EVENT_INFO = {
    "start_rank": "ranked by start time",
    "end_rank": "ranked by end time",
    "duration_rank": "ranked by duration",
}

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_'-]+|[^\w\s]", re.UNICODE)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def format_time(event: dict[str, Any]) -> str:
    return str(event["ts"]) if event["ts"] == event["te"] else f"{event['ts']} -> {event['te']}"


def event_line(event: dict[str, Any]) -> str:
    return f"{event['s_text']} --{event['r_text']}--> {event['o_text']} [{format_time(event)}]"


def ordinal_en(rank: int) -> str:
    if 10 <= rank % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
    return f"{rank}{suffix}"


def tokenize_text(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


def sample_priority(sample: dict[str, Any], focus_code: str, rng: random.Random) -> tuple[int, int, int, int, float]:
    meta = sample["metadata"]
    allen_codes = list(meta["allen_codes"])
    multi_types = list(meta["multi_event_temporal_types"])
    pure_one_allen = int(not (len(allen_codes) == 1 and not multi_types))
    one_allen = int(len(allen_codes) != 1)
    return (
        pure_one_allen,
        one_allen,
        int(meta["temporal_constraint_count"]),
        int(meta["forward_transition_count"]) + int(meta["backward_constraint_count"]),
        rng.random(),
    )


def find_focus_step(sample: dict[str, Any], focus_code: str) -> dict[str, Any] | None:
    for step in sample["steps"]:
        payload = step.get("constraint_payload") or {}
        if payload.get("allen_code") == focus_code:
            return step
    return None


def temporal_constraints(sample: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step in sample["steps"]:
        if step["step_kind"] != "temporal_filter":
            continue
        payload = step["constraint_payload"]
        if "allen_code" in payload:
            code = str(payload["allen_code"])
            rows.append(
                {
                    "type": "allen",
                    "code": code,
                    "name_zh": ALLEN_INFO[code]["zh"],
                    "description_en": ALLEN_INFO[code]["en"],
                    "step_index": step["step_index"],
                }
            )
        elif "multi_event_type" in payload:
            mtype = str(payload["multi_event_type"])
            rows.append(
                {
                    "type": "multi_event",
                    "code": mtype,
                    "description_en": f"{MULTI_EVENT_INFO[mtype]}, keep rank {payload['rank']}",
                    "step_index": step["step_index"],
                }
            )
    return rows


def _root_phrase(sample: dict[str, Any]) -> str:
    seed = sample["seed"]
    relation_text = seed["fixed_relation_text"]
    if seed["seed_mode"] == "sr":
        return f'Which entity is linked from {seed["fixed_subject_text"]} by the relation "{relation_text}"'
    return f'Which entity has the relation "{relation_text}" to {seed["fixed_object_text"]}'


def _seed_event_label(sample: dict[str, Any]) -> str:
    """Return a short human-readable label for the seed (root) event."""
    seed = sample["seed"]
    rel = seed["fixed_relation_text"]
    if seed["seed_mode"] == "sr":
        return f'"{rel}" (from {seed["fixed_subject_text"]})'
    return f'"{rel}" (to {seed["fixed_object_text"]})'


def _render_temporal_clause(step: dict[str, Any], sample: dict[str, Any]) -> str:
    payload = step["constraint_payload"]
    if "allen_code" in payload:
        code = str(payload["allen_code"])
        template = ALLEN_YEAR_DESC[code]
        if "backward_relation" in payload:
            rel = str(payload["backward_relation"])
            obj = str(payload["backward_object"])
            relation_text = rel
            object_text = obj
            for event in sample["participating_events"]:
                if event["r"] == rel and event["o"] == obj:
                    relation_text = event["r_text"]
                    object_text = event["o_text"]
                    break
            label_a = _seed_event_label(sample)
            label_b = f'"{relation_text}" (to {object_text})'
            desc = template.format(A=label_a, B=label_b)
            return f'where {desc}'
        if "reference_entity" in payload:
            ref_text = str(payload.get("reference_entity_text", payload["reference_entity"]))
            seed = sample.get("seed", {})
            relation_text = seed.get("fixed_relation_text", "?")
            label_a = f'the candidate\'s "{relation_text}" period'
            label_b = f'{ref_text}\'s "{relation_text}" period'
            desc = template.format(A=label_a, B=label_b)
            return f'where {desc}'
        if "forward_relation" in payload:
            relation_text = str(payload["forward_relation"])
            for event in sample["participating_events"]:
                if event["r"] == payload["forward_relation"]:
                    relation_text = event["r_text"]
                    break
            label_a = "the current step"
            label_b = f'"{relation_text}"'
            desc = template.format(A=label_a, B=label_b)
            return f'where {desc}'
        label_a = "the previous step"
        label_b = "the current step"
        desc = template.format(A=label_a, B=label_b)
        return f'where {desc}'

    metric = str(payload["multi_event_type"])
    rank = int(payload["rank"])
    return f'and among the current candidates, keep the entity ranked {ordinal_en(rank)} by {MULTI_EVENT_INFO[metric]}'


def _render_backward_clause(step: dict[str, Any], sample: dict[str, Any]) -> str:
    payload = step["constraint_payload"]
    rel = str(payload["backward_relation"])
    obj = str(payload["backward_object"])
    relation_text = rel
    object_text = obj
    for event in sample["participating_events"]:
        if event["r"] == rel and event["o"] == obj:
            relation_text = event["r_text"]
            object_text = event["o_text"]
            break
    return f'and also has the relation "{relation_text}" to {object_text}'


def _render_forward_sentence(step: dict[str, Any], sample: dict[str, Any]) -> str:
    relation = str(step["constraint_payload"]["forward_relation"])
    relation_text = relation
    for event in sample["participating_events"]:
        if event["r"] == relation:
            relation_text = event["r_text"]
            break
    return f'Following the unique remaining entity via the relation "{relation_text}", which next entity'


def build_agent_question(sample: dict[str, Any]) -> dict[str, Any]:
    sentences: list[str] = []
    traces: list[dict[str, str]] = []
    current = _root_phrase(sample)

    for step in sample["steps"][1:]:
        if step["step_kind"] == "backward_filter":
            clause = _render_backward_clause(step, sample)
            current = f"{current} {clause}"
            traces.append({"agent": "structure_planner", "output": clause})
            continue
        if step["step_kind"] == "temporal_filter":
            clause = _render_temporal_clause(step, sample)
            current = f"{current} {clause}"
            traces.append({"agent": "temporal_planner", "output": clause})
            continue
        if step["step_kind"] == "forward_transition":
            sentences.append(current + "?")
            current = _render_forward_sentence(step, sample)
            traces.append({"agent": "forward_router", "output": current})

    sentences.append(current + "?")
    question = " ".join(sentences)
    return {
        "question": question,
        "trace": traces,
    }


def tc_refs(sample: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract reference entity + time for each temporal_filter step."""
    refs: list[dict[str, Any]] = []
    events_by_fid = {ev["fid"]: ev for ev in sample.get("participating_events", [])}
    for step in sample["steps"]:
        if step["step_kind"] != "temporal_filter":
            continue
        payload = step.get("constraint_payload", {})
        ref_text = payload.get("reference_entity_text", "")
        ref_fid = payload.get("reference_fact_fid")
        ts, te = None, None
        if ref_fid and ref_fid in events_by_fid:
            ev = events_by_fid[ref_fid]
            ts, te = ev.get("ts"), ev.get("te")
        refs.append({"text": ref_text, "start": ts, "end": te})
    return refs


def sample_answer(sample: dict[str, Any]) -> dict[str, Any]:
    """Return the answer object across old and current trace schemas."""
    answer = sample.get("final_answer", sample.get("answer"))
    if isinstance(answer, dict):
        return {
            "id": answer.get("id", ""),
            "text": answer.get("text", ""),
        }
    return {
        "id": "",
        "text": "" if answer is None else str(answer),
    }


def build_benchmark_record(sample: dict[str, Any], focus_code: str, index_within_code: int, global_id: int = 0) -> dict[str, Any]:
    agent = build_agent_question(sample)
    focus_step = find_focus_step(sample, focus_code)
    if focus_step is None:
        raise ValueError(f"focus code {focus_code} not found in sample {sample['sample_id']}")
    answer = sample_answer(sample)
    subgraph_lines = [event_line(event) for event in sample["participating_events"]]
    constraints = temporal_constraints(sample)
    return {
        "benchmark_id": f"{focus_code}_test_{index_within_code:02d}",
        "source_sample_id": sample["sample_id"],
        "template_id": sample["template_id"],
        "graph_structure": sample["graph_structure"],
        "hop_count": sample["hop_count"],
        "focus_allen_code": focus_code,
        "focus_allen_name_zh": ALLEN_INFO[focus_code]["zh"],
        "focus_allen_description_en": ALLEN_INFO[focus_code]["en"],
        "focus_temporal_step_index": focus_step["step_index"],
        "seed": sample["seed"],
        "steps": sample["steps"],
        "answer": answer,
        "participating_events": sample["participating_events"],
        "subgraph_lines": subgraph_lines,
        "temporal_constraints": constraints,
        "tc_ref": tc_refs(sample),
        "agent_trace": agent["trace"],
        "gold_question": agent["question"],
        "original_question_en": agent["question"],
    }
