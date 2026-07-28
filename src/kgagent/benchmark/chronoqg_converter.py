"""Converter from NormalizedTKG to ChronoQG TSV format."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kgagent.benchmark.types import NormalizedTKG, TimeInfo


def convert_tkg_to_chronoqg_format(
    tkg: NormalizedTKG,
    output_dir: Path,
    time_granularity: str = "year",
) -> dict[str, Path]:
    """Convert NormalizedTKG to ChronoQG input format.

    ChronoQG requires:
    - full.txt: subject<TAB>relation<TAB>object<TAB>start<TAB>end
    - wd_id2entity_text.txt: entity_id<TAB>entity_text
    - wd_id2relation_text.txt: relation_id<TAB>relation_text

    Args:
        tkg: Normalized temporal knowledge graph
        output_dir: Directory to save converted files
        time_granularity: "year", "month", or "day"

    Returns:
        Dict with paths to generated files
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build entity mapping: id -> text
    entity_map: dict[str, str] = {}
    for entity in tkg["entities"]:
        entity_map[entity["id"]] = entity["name"]

    # Build relation mapping: relation -> text (use relation itself as text if no mapping)
    relation_map: dict[str, str] = {}
    for fact in tkg["temporal_facts"]:
        if fact["relation"] not in relation_map:
            relation_map[fact["relation"]] = fact["relation"]  # Use relation as display text

    # Write entity mapping
    entity_file = output_dir / "wd_id2entity_text.txt"
    with open(entity_file, "w", encoding="utf-8") as f:
        for entity_id, entity_text in sorted(entity_map.items()):
            f.write(f"{entity_id}\t{entity_text}\n")

    # Write relation mapping
    relation_file = output_dir / "wd_id2relation_text.txt"
    with open(relation_file, "w", encoding="utf-8") as f:
        for relation_id, relation_text in sorted(relation_map.items()):
            f.write(f"{relation_id}\t{relation_text}\n")

    # Write temporal facts
    full_file = output_dir / "full.txt"
    with open(full_file, "w", encoding="utf-8") as f:
        for fact in tkg["temporal_facts"]:
            subject = fact["subject"]
            relation = fact["relation"]
            obj = fact["object"]

            # Convert time to ChronoQG format
            time_start, time_end = _convert_time_to_chronoqg(fact["time"], time_granularity)

            # Skip invalid time facts
            if time_start is None or time_end is None:
                continue

            # ChronoQG requires start <= end
            if time_start > time_end:
                continue

            f.write(f"{subject}\t{relation}\t{obj}\t{time_start}\t{time_end}\n")

    return {
        "kg_file": full_file,
        "entity_map": entity_file,
        "relation_map": relation_file,
    }


def _convert_time_to_chronoqg(time_info: TimeInfo | dict, granularity: str) -> tuple[int | None, int | None]:
    """Convert TimeInfo to ChronoQG integer format.

    ChronoQG time formats:
    - year: YYYY (e.g., 1999)
    - month: YYYYMM (e.g., 199905)
    - day: YYYYMMDD (e.g., 19990523)

    Args:
        time_info: TimeInfo object or dict
        granularity: "year", "month", or "day"

    Returns:
        Tuple of (start, end) as integers, or (None, None) if conversion fails
    """
    # Handle dict format
    if isinstance(time_info, dict):
        time_type = time_info.get("type", "point")
        if time_type == "point":
            value = _parse_time_value(time_info.get("value"), granularity)
            return (value, value)
        elif time_type == "interval":
            start_val = _parse_time_value(time_info.get("start"), granularity) if time_info.get("start") else None
            end_val = _parse_time_value(time_info.get("end"), granularity) if time_info.get("end") else None
            return (start_val, end_val)
    else:
        # Handle TimeInfo object (for type checking)
        if time_info["type"] == "point":
            value = _parse_time_value(time_info["value"], granularity)
            return (value, value)
        elif time_info["type"] == "interval":
            start_val = _parse_time_value(time_info.get("start"), granularity) if time_info.get("start") else None
            end_val = _parse_time_value(time_info.get("end"), granularity) if time_info.get("end") else None
            return (start_val, end_val)

    return (None, None)


def _parse_time_value(value: Any, granularity: str) -> int | None:
    """Parse time value string to ChronoQG integer format.

    Supports various input formats:
    - ISO format: "1999-05-23", "1999-05", "1999"
    - ChronoQG format: 19990523, 199905, 1999
    - Year only: 1999

    Args:
        value: Time value (str or int)
        granularity: Target granularity

    Returns:
        Integer in ChronoQG format, or None if parsing fails
    """
    if value is None:
        return None

    # Already an integer
    if isinstance(value, int):
        return value

    # Convert to string and normalize
    value_str = str(value).strip()

    # Remove common separators
    value_str = value_str.replace("-", "").replace("/", "").replace(":", "").replace(" ", "")

    # Try to extract year, month, day
    try:
        if len(value_str) >= 8:
            # Full date: YYYYMMDD
            year = int(value_str[0:4])
            month = int(value_str[4:6])
            day = int(value_str[6:8])
        elif len(value_str) >= 6:
            # Year-month: YYYYMM
            year = int(value_str[0:4])
            month = int(value_str[4:6])
            day = 1
        elif len(value_str) == 4:
            # Year only: YYYY
            year = int(value_str)
            month = 1
            day = 1
        else:
            return None

        # Convert to target granularity
        if granularity == "year":
            return year
        elif granularity == "month":
            return year * 100 + month
        elif granularity == "day":
            return year * 10000 + month * 100 + day
        else:
            return None

    except (ValueError, IndexError):
        return None


def estimate_time_granularity(tkg: NormalizedTKG) -> str:
    """Estimate appropriate time granularity from TKG data.

    Args:
        tkg: Normalized temporal knowledge graph

    Returns:
        "year", "month", or "day"
    """
    # Sample a few temporal facts to determine granularity
    sample_size = min(100, len(tkg["temporal_facts"]))

    has_day_precision = False
    has_month_precision = False

    for fact in tkg["temporal_facts"][:sample_size]:
        time_val = None

        if fact["time"]["type"] == "point":
            time_val = str(fact["time"]["value"])
        elif fact["time"]["type"] == "interval" and fact["time"].get("start"):
            time_val = str(fact["time"]["start"])

        if time_val:
            # Remove separators
            time_val = time_val.replace("-", "").replace("/", "")

            # Check precision
            if len(time_val) >= 8:
                has_day_precision = True
            elif len(time_val) >= 6:
                has_month_precision = True

    # Return finest granularity found
    if has_day_precision:
        return "day"
    elif has_month_precision:
        return "month"
    else:
        return "year"
