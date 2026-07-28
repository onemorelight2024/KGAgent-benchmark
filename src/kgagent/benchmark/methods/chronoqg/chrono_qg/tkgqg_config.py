"""
TKGQG Pipeline Configuration.

Centralises all paths, model settings, and the **time granularity** that
governs how Allen descriptions, prompt wording, and effective_time_range
behave.  Every downstream module should import from here instead of
hard-coding paths or granularity-sensitive strings.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

# ── granularity type ────────────────────────────────────────────────
TimeGranularity = Literal["year", "month", "day"]


# ── Allen description templates, keyed by granularity ───────────────
# {A} = first event label, {B} = second event label.
# "year" → existing ALLEN_YEAR_DESC; "day"/"month" swap the wording.

_ALLEN_DESC_YEAR: dict[str, str] = {
    # interval-interval
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
    # point-interval
    "tptr-14": "the {A} event occurred before the {B} period began",
    "tptr-15": "the {A} event occurred in the same year the {B} period began",
    "tptr-16": "the {A} event occurred during the {B} period",
    "tptr-17": "the {A} event occurred in the same year the {B} period ended",
    "tptr-18": "the {A} event occurred after the {B} period ended",
    # interval-point
    "trtp-19": "the {A} period ended before the {B} event",
    "trtp-20": "the {A} period ended in the same year as the {B} event",
    "trtp-21": "the {A} period was ongoing during the year of the {B} event",
    "trtp-22": "the {A} period began in the same year as the {B} event",
    "trtp-23": "the {A} period began after the {B} event",
    # point-point
    "tp-24": "the {A} event happened in an earlier year than the {B} event",
    "tp-25": "the {A} event happened in the same year as the {B} event",
    "tp-26": "the {A} event happened in a later year than the {B} event",
}

_ALLEN_DESC_DAY: dict[str, str] = {
    # interval-interval
    "tr-1":  "the {A} period ended before the {B} period began",
    "tr-2":  "the {A} period ended on the exact day the {B} period began",
    "tr-3":  "the {A} period started earlier and overlapped with the {B} period for some days",
    "tr-4":  "the {A} period started earlier but ended on the same day as the {B} period",
    "tr-5":  "the {A} period fully encompassed the {B} period",
    "tr-6":  "the {A} period started on the same day as the {B} period but ended earlier",
    "tr-7":  "the {A} period spanned exactly the same dates as the {B} period",
    "tr-8":  "the {A} period started on the same day as the {B} period but lasted longer",
    "tr-9":  "the {A} period fell entirely within the {B} period",
    "tr-10": "the {A} period started later but ended on the same day as the {B} period",
    "tr-11": "the {A} period started later and overlapped with the tail end of the {B} period",
    "tr-12": "the {A} period began on the exact day the {B} period ended",
    "tr-13": "the {A} period began after the {B} period had ended",
    # point-interval
    "tptr-14": "the {A} event occurred before the {B} period began",
    "tptr-15": "the {A} event occurred on the same day the {B} period began",
    "tptr-16": "the {A} event occurred during the {B} period",
    "tptr-17": "the {A} event occurred on the same day the {B} period ended",
    "tptr-18": "the {A} event occurred after the {B} period ended",
    # interval-point
    "trtp-19": "the {A} period ended before the {B} event",
    "trtp-20": "the {A} period ended on the same day as the {B} event",
    "trtp-21": "the {A} period was ongoing on the day of the {B} event",
    "trtp-22": "the {A} period began on the same day as the {B} event",
    "trtp-23": "the {A} period began after the {B} event",
    # point-point
    "tp-24": "the {A} event happened on an earlier date than the {B} event",
    "tp-25": "the {A} event happened on the same day as the {B} event",
    "tp-26": "the {A} event happened on a later date than the {B} event",
}

_ALLEN_DESC_MONTH: dict[str, str] = {
    # interval-interval
    "tr-1":  "the {A} period ended before the {B} period began",
    "tr-2":  "the {A} period ended in the same month the {B} period began",
    "tr-3":  "the {A} period started earlier and overlapped with the {B} period for some months",
    "tr-4":  "the {A} period started earlier but ended in the same month as the {B} period",
    "tr-5":  "the {A} period fully encompassed the {B} period",
    "tr-6":  "the {A} period started in the same month as the {B} period but ended earlier",
    "tr-7":  "the {A} period spanned exactly the same months as the {B} period",
    "tr-8":  "the {A} period started in the same month as the {B} period but lasted longer",
    "tr-9":  "the {A} period fell entirely within the {B} period",
    "tr-10": "the {A} period started later but ended in the same month as the {B} period",
    "tr-11": "the {A} period started later and overlapped with the tail end of the {B} period",
    "tr-12": "the {A} period began in the same month the {B} period ended",
    "tr-13": "the {A} period began after the {B} period had ended",
    # point-interval
    "tptr-14": "the {A} event occurred before the {B} period began",
    "tptr-15": "the {A} event occurred in the same month the {B} period began",
    "tptr-16": "the {A} event occurred during the {B} period",
    "tptr-17": "the {A} event occurred in the same month the {B} period ended",
    "tptr-18": "the {A} event occurred after the {B} period ended",
    # interval-point
    "trtp-19": "the {A} period ended before the {B} event",
    "trtp-20": "the {A} period ended in the same month as the {B} event",
    "trtp-21": "the {A} period was ongoing during the month of the {B} event",
    "trtp-22": "the {A} period began in the same month as the {B} event",
    "trtp-23": "the {A} period began after the {B} event",
    # point-point
    "tp-24": "the {A} event happened in an earlier month than the {B} event",
    "tp-25": "the {A} event happened in the same month as the {B} event",
    "tp-26": "the {A} event happened in a later month than the {B} event",
}

ALLEN_DESC_BY_GRANULARITY: dict[TimeGranularity, dict[str, str]] = {
    "year":  _ALLEN_DESC_YEAR,
    "month": _ALLEN_DESC_MONTH,
    "day":   _ALLEN_DESC_DAY,
}

# ── Strict Allen definitions, keyed by granularity ──────────────────
# These go into verification prompts verbatim.

_STRICT_YEAR = {
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
    "start_rank":    "Rank entities by their start year (ts) in ascending order.",
    "end_rank":      "Rank entities by their end year (te) in ascending order.",
    "duration_rank": "Rank entities by duration (te - ts) in ascending order.",
}

_STRICT_DAY = {
    "tr-1":  "te(A) < ts(B)  — A ends strictly before B starts.",
    "tr-2":  "te(A) == ts(B) — A ends on the exact same date B starts.",
    "tr-3":  "ts(A) < ts(B) AND ts(B) < te(A) AND te(A) < te(B)  — A starts first, they overlap, B outlasts A.",
    "tr-4":  "ts(A) < ts(B) AND te(A) == te(B) — A starts earlier, both end on the same date.",
    "tr-5":  "ts(A) < ts(B) AND te(B) < te(A) — A strictly contains B.",
    "tr-6":  "ts(A) == ts(B) AND te(A) < te(B) — same start date, A ends earlier.",
    "tr-7":  "ts(A) == ts(B) AND te(A) == te(B) — identical start and end dates.",
    "tr-8":  "ts(A) == ts(B) AND te(A) > te(B) — same start date, A ends later.",
    "tr-9":  "ts(B) < ts(A) AND te(A) < te(B) — A is strictly inside B.",
    "tr-10": "ts(A) > ts(B) AND te(A) == te(B) — A starts later, both end on the same date.",
    "tr-11": "ts(A) > ts(B) AND ts(A) < te(B) AND te(A) > te(B) — A starts later but overlaps B's tail.",
    "tr-12": "ts(A) == te(B) — A starts on the exact date B ends.",
    "tr-13": "ts(A) > te(B) — A starts strictly after B ends.",
    "tptr-14": "ts(A) < ts(B) — point A is strictly before B's start.",
    "tptr-15": "ts(A) == ts(B) — point A is on the same date B starts.",
    "tptr-16": "ts(B) < ts(A) AND ts(A) < te(B) — point A is strictly inside B.",
    "tptr-17": "ts(A) == te(B) — point A is on the same date B ends.",
    "tptr-18": "ts(A) > te(B) — point A is strictly after B ends.",
    "trtp-19": "te(A) < ts(B) — A ends strictly before point B.",
    "trtp-20": "te(A) == ts(B) — A ends on the same date as point B.",
    "trtp-21": "ts(A) < ts(B) AND ts(B) < te(A) — point B is strictly inside A.",
    "trtp-22": "ts(A) == ts(B) — A starts on the same date as point B.",
    "trtp-23": "ts(A) > ts(B) — A starts strictly after point B.",
    "tp-24":  "ts(A) < ts(B) — A happened on a strictly earlier date than B.",
    "tp-25":  "ts(A) == ts(B) — A and B happened on the same date.",
    "tp-26":  "ts(A) > ts(B) — A happened on a strictly later date than B.",
    "start_rank":    "Rank entities by their start date (ts) in ascending order.",
    "end_rank":      "Rank entities by their end date (te) in ascending order.",
    "duration_rank": "Rank entities by duration (te - ts) in ascending order.",
}

# month reuses day definitions (logic is identical; wording already generic)
_STRICT_MONTH = _STRICT_DAY

ALLEN_STRICT_BY_GRANULARITY: dict[TimeGranularity, dict[str, str]] = {
    "year":  _STRICT_YEAR,
    "month": _STRICT_MONTH,
    "day":   _STRICT_DAY,
}

# ── Prompt timestamp preamble ───────────────────────────────────────
TIMESTAMP_PREAMBLE: dict[TimeGranularity, str] = {
    "year":  "All timestamps are integer years.",
    "month": "All timestamps are dates at month granularity (YYYY-MM).",
    "day":   "All timestamps are dates at day granularity (YYYY-MM-DD).",
}

# ── Unit label for effective_time_range point-expansion ─────────────
POINT_EXPANSION_UNIT: dict[TimeGranularity, int] = {
    "year":  1,   # ts==te for interval → ts, ts+1
    "month": 1,   # ts==te for interval → ts, ts+1 (month integer)
    "day":   1,   # ts==te for interval → ts, ts+1 (day ordinal)
}


# ===================================================================
#  Pipeline configuration dataclass
# ===================================================================

@dataclass
class PipelineConfig:
    """All settings needed to run the TKGQG pipeline end-to-end."""

    # ── KG paths ────────────────────────────────────────────────────
    kg_path: Path = Path("full.txt")
    entity_map_path: Path = Path("wd_id2entity_text.txt")
    relation_map_path: Path = Path("wd_id2relation_text.txt")

    # ── Relation classification (optional; if absent, all treated as I)
    relation_type_tsv: Path | None = None

    # ── Output root ─────────────────────────────────────────────────
    output_dir: Path = Path("output")

    # ── Time granularity ────────────────────────────────────────────
    time_granularity: TimeGranularity = "year"

    # ── Sampling parameters ─────────────────────────────────────────
    min_seed_answers: int = 5
    max_seed_answers: int = 50
    max_forward_answers: int = 50
    max_instances_per_answer: int = 8
    max_time: int = 2100       # filter future timestamps
    attempts_per_seed_template: int = 4
    random_seed: int = 13

    # ── Benchmark build ─────────────────────────────────────────────
    per_code: int = 10

    # ── Sample template balance ──────────────────────────────────────
    # Cap per-template trace count per sampling round (tc1 / any).
    # e.g. {"ro_seed_self_allen": 5000} stops that template after 5000 traces.
    max_per_template: dict[str, int] = field(default_factory=dict)

    # ── LLM settings ────────────────────────────────────────────────
    rewrite_model: str = "gpt-4o-mini"
    answer_model: str = "gpt-5"
    judge_model: str = "gpt-4o-mini"
    api_base_url: str = ""
    api_key: str = ""
    parallelism: int = 64

    # ── Deleted relations (KG-specific) ─────────────────────────────
    deleted_relations: set[str] = field(default_factory=set)

    # ── Derived helpers ─────────────────────────────────────────────
    @property
    def allen_desc(self) -> dict[str, str]:
        return ALLEN_DESC_BY_GRANULARITY[self.time_granularity]

    @property
    def allen_strict_def(self) -> dict[str, str]:
        return ALLEN_STRICT_BY_GRANULARITY[self.time_granularity]

    @property
    def timestamp_preamble(self) -> str:
        return TIMESTAMP_PREAMBLE[self.time_granularity]

    @property
    def point_expansion(self) -> int:
        return POINT_EXPANSION_UNIT[self.time_granularity]

    # ── Serialization ───────────────────────────────────────────────
    def save(self, path: Path) -> None:
        d = asdict(self)
        # convert Path / set to JSON-friendly types
        for k, v in d.items():
            if isinstance(v, Path):
                d[k] = str(v)
            elif isinstance(v, (set, frozenset)):
                d[k] = sorted(v)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(d, ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: Path) -> PipelineConfig:
        raw = json.loads(path.read_text())
        raw.pop("baseline_model", None)
        # restore types
        for k in ("kg_path", "entity_map_path", "relation_map_path",
                   "relation_type_tsv", "output_dir"):
            if k in raw and raw[k] is not None:
                raw[k] = Path(raw[k])
        if "deleted_relations" in raw:
            raw["deleted_relations"] = set(raw["deleted_relations"])
        return cls(**raw)

    def update_deleted(self, deleted: list[str]) -> None:
        self.deleted_relations = set(deleted)
