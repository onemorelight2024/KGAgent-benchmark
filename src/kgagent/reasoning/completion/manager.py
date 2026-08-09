from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Tuple

from kgagent.reasoning.completion.methods import COMPLETION_METHODS


Triple = Tuple[str, str, str]


def list_kg_completion_methods() -> list[dict[str, Any]]:
    return [dict(method) for method in COMPLETION_METHODS]


def recommend_kg_completion_methods(task_type: str, mode: str | None = None) -> list[dict[str, Any]]:
    normalized_task = task_type.strip().lower()
    normalized_mode = (mode or "").strip().lower()
    candidates = [
        dict(method)
        for method in COMPLETION_METHODS
        if normalized_task in method.get("task_types", [])
    ]
    for method in candidates:
        supports = set(method.get("supports", []))
        method["recommended"] = not normalized_mode or normalized_mode in supports
        method["recommendation_reason"] = (
            "Recommended because KICGPT is a training-free, LLM-in-context link "
            "prediction baseline for missing-head and missing-tail KG completion."
        )
    return candidates


def run_kicgpt_kg_completion_for_agent(
    input_data: Mapping[str, Any],
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    triples = _normalize_triples(input_data)
    if not triples:
        raise ValueError("kg.triples must contain at least one triple.")

    query = _normalize_query(input_data)
    mode = _infer_mode(input_data, query)
    if mode not in {"tail_prediction", "head_prediction"}:
        raise ValueError("KICGPT v1 supports only tail_prediction and head_prediction.")

    top_k = max(1, int(input_data.get("top_k", 10)))
    candidate_limit = max(top_k, int(input_data.get("candidate_limit", max(20, top_k))))
    working_dir = _completion_working_dir(input_data, triples, workspace=workspace)
    reused_existing = (working_dir / ".kgagent_kicgpt_ready.json").exists()
    working_dir.mkdir(parents=True, exist_ok=True)

    graph = _build_graph(triples)
    predictions = _rank_candidates(
        triples=triples,
        graph=graph,
        query=query,
        mode=mode,
        limit=candidate_limit,
    )[:top_k]

    context = _build_kicgpt_context(
        triples=triples,
        predictions=predictions,
        query=query,
        mode=mode,
    )
    warnings = _completion_warnings(triples)

    dataset_snapshot = {
        "triples": [{"source": h, "relation": r, "target": t} for h, r, t in triples],
        "query": query,
        "mode": mode,
        "candidate_count": len(predictions),
    }
    (working_dir / "kgagent_kicgpt_dataset.json").write_text(
        json.dumps(dataset_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (working_dir / "kgagent_kicgpt_context.json").write_text(
        json.dumps(context, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_kicgpt_compatible_dataset(working_dir, triples=triples, query=query, predictions=predictions, mode=mode)
    _write_ready_marker(working_dir, triples=triples)

    return {
        "task_type": "kg_completion",
        "method": "kicgpt",
        "mode": mode,
        "query": query,
        "predictions": predictions,
        "kicgpt_context": context,
        "working_dir": str(working_dir),
        "reused_existing": reused_existing,
        "built_index": not reused_existing,
        "warnings": warnings,
        "summary": {
            "triple_count": len(triples),
            "entity_count": len(_entities(triples)),
            "relation_count": len({r for _, r, _ in triples}),
            "prediction_count": len(predictions),
        },
    }


def _normalize_triples(input_data: Mapping[str, Any]) -> list[Triple]:
    raw_kg = input_data.get("kg")
    if raw_kg is None and input_data.get("kg_input") is not None:
        raw_kg = _load_completion_kg_input(input_data.get("kg_input"), input_data=input_data)
    if raw_kg is None:
        raw_kg = input_data
    raw_triples = raw_kg.get("triples") if isinstance(raw_kg, Mapping) else raw_kg
    if not isinstance(raw_triples, list):
        raise ValueError("Expected kg.triples to be a list.")

    triples: list[Triple] = []
    seen: set[Triple] = set()
    for item in raw_triples:
        triple = _normalize_triple_item(item)
        if triple and triple not in seen:
            seen.add(triple)
            triples.append(triple)
    return triples


def _load_completion_kg_input(raw_input: Any, *, input_data: Mapping[str, Any]) -> Any:
    if raw_input is None:
        return None
    if isinstance(raw_input, Mapping):
        return raw_input

    if isinstance(raw_input, str):
        text = raw_input.strip()
        if not text:
            return None
        path = Path(text).expanduser()
        if not path.is_absolute():
            workspace = input_data.get("workspace")
            if isinstance(workspace, str) and workspace.strip():
                path = Path(workspace).expanduser().resolve() / path
        if path.exists() and path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                if loaded and isinstance(loaded[0], Mapping) and "kg" in loaded[0]:
                    first = loaded[0]
                    if isinstance(first, Mapping):
                        kg_value = first.get("kg")
                        if kg_value is not None:
                            return {"triples": kg_value}
                return {"triples": loaded}
            if isinstance(loaded, Mapping):
                if "kg" in loaded and isinstance(loaded["kg"], list):
                    return {"triples": loaded["kg"]}
                if "triples" in loaded and isinstance(loaded["triples"], list):
                    return loaded
            return loaded

        loaded = json.loads(text)
        if isinstance(loaded, Mapping) or isinstance(loaded, list):
            return loaded
    return raw_input


def _normalize_triple_item(item: Any) -> Triple | None:
    if isinstance(item, str):
        parsed = _parse_tagged_triple(item)
        if parsed is not None:
            return parsed
        return None

    if isinstance(item, Mapping):
        source = item.get("source", item.get("head", item.get("subject")))
        relation = item.get("relation", item.get("predicate"))
        target = item.get("target", item.get("tail", item.get("object")))
    elif isinstance(item, (list, tuple)) and len(item) >= 3:
        source, relation, target = item[0], item[1], item[2]
    else:
        return None

    values = [_clean_value(source), _clean_value(relation), _clean_value(target)]
    if any(not value for value in values):
        return None
    return values[0], values[1], values[2]


def _parse_tagged_triple(text: str) -> Triple | None:
    match = re.search(r"<subj>\s*(.*?)\s*<obj>\s*(.*?)\s*<rel>\s*(.*)", text)
    if match:
        source, target, relation = (group.strip() for group in match.groups())
        if source and relation and target:
            return source, relation, target
    for sep in ["\t", "|"]:
        parts = [part.strip() for part in text.split(sep)]
        if len(parts) >= 3 and all(parts[:3]):
            return parts[0], parts[1], parts[2]
    return None


def _normalize_query(input_data: Mapping[str, Any]) -> dict[str, str]:
    raw_query = input_data.get("query", {})
    if not isinstance(raw_query, Mapping):
        raise ValueError("query must be an object with source/relation/target.")
    return {
        "source": _clean_value(raw_query.get("source", raw_query.get("head", raw_query.get("subject", "")))),
        "relation": _clean_value(raw_query.get("relation", raw_query.get("predicate", ""))),
        "target": _clean_value(raw_query.get("target", raw_query.get("tail", raw_query.get("object", "")))),
    }


def _infer_mode(input_data: Mapping[str, Any], query: Mapping[str, str]) -> str:
    mode = _clean_value(input_data.get("mode", "")).lower()
    if mode in {"tail", "missing_tail"}:
        return "tail_prediction"
    if mode in {"head", "missing_head"}:
        return "head_prediction"
    if mode in {"tail_prediction", "head_prediction"}:
        return mode
    if query.get("target") in {"", "?", "[mask]", "[MASK]"}:
        return "tail_prediction"
    if query.get("source") in {"", "?", "[mask]", "[MASK]"}:
        return "head_prediction"
    raise ValueError("Cannot infer mode. Use '?' for the missing source or target.")


def _build_graph(triples: Iterable[Triple]) -> dict[str, Any]:
    out_edges: dict[str, list[Triple]] = defaultdict(list)
    in_edges: dict[str, list[Triple]] = defaultdict(list)
    relation_pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for h, r, t in triples:
        out_edges[h].append((h, r, t))
        in_edges[t].append((h, r, t))
        relation_pairs[r].append((h, t))
    return {
        "out_edges": out_edges,
        "in_edges": in_edges,
        "relation_pairs": relation_pairs,
    }


def _rank_candidates(
    *,
    triples: list[Triple],
    graph: Mapping[str, Any],
    query: Mapping[str, str],
    mode: str,
    limit: int,
) -> list[dict[str, Any]]:
    entities = sorted(_entities(triples))
    existing = set(triples)
    relation = query["relation"]
    known_entity = query["source"] if mode == "tail_prediction" else query["target"]
    relation_pairs = graph["relation_pairs"].get(relation, [])

    scored: list[dict[str, Any]] = []
    for candidate in entities:
        if mode == "tail_prediction":
            source, target = query["source"], candidate
            if source == target or (source, relation, target) in existing:
                continue
        else:
            source, target = candidate, query["target"]
            if source == target or (source, relation, target) in existing:
                continue

        evidence = _candidate_evidence(
            triples=triples,
            graph=graph,
            known_entity=known_entity,
            candidate=candidate,
            relation=relation,
        )
        score = _candidate_score(
            candidate=candidate,
            known_entity=known_entity,
            relation=relation,
            relation_pairs=relation_pairs,
            evidence=evidence,
            mode=mode,
        )
        if score <= 0:
            continue
        scored.append(
            {
                "source": source,
                "relation": relation,
                "target": target,
                "candidate": candidate,
                "score": round(min(score, 0.99), 4),
                "evidence": evidence[:6],
                "reason": _candidate_reason(mode, known_entity, candidate, relation, evidence),
            }
        )

    scored.sort(key=lambda item: (-float(item["score"]), item["candidate"]))
    return scored[:limit]


def _candidate_evidence(
    *,
    triples: list[Triple],
    graph: Mapping[str, Any],
    known_entity: str,
    candidate: str,
    relation: str,
) -> list[str]:
    evidence: list[str] = []
    for h, r, t in triples:
        if r == relation and (h == known_entity or t == known_entity or h == candidate or t == candidate):
            evidence.append(_format_triple((h, r, t)))
    for triple in graph["out_edges"].get(known_entity, []) + graph["in_edges"].get(known_entity, []):
        evidence.append(_format_triple(triple))
    for triple in graph["out_edges"].get(candidate, []) + graph["in_edges"].get(candidate, []):
        evidence.append(_format_triple(triple))
    return _dedupe(evidence)


def _candidate_score(
    *,
    candidate: str,
    known_entity: str,
    relation: str,
    relation_pairs: list[tuple[str, str]],
    evidence: list[str],
    mode: str,
) -> float:
    score = 0.05
    if mode == "tail_prediction":
        if any(t == candidate for _, t in relation_pairs):
            score += 0.35
        if any(h == known_entity for h, _ in relation_pairs):
            score += 0.15
    else:
        if any(h == candidate for h, _ in relation_pairs):
            score += 0.35
        if any(t == known_entity for _, t in relation_pairs):
            score += 0.15
    score += min(len(evidence), 6) * 0.06
    score += _token_overlap(candidate, known_entity) * 0.08
    score += _relation_name_hint(candidate, relation) * 0.05
    return score


def _build_kicgpt_context(
    *,
    triples: list[Triple],
    predictions: list[dict[str, Any]],
    query: Mapping[str, str],
    mode: str,
) -> dict[str, Any]:
    relation = query["relation"]
    analogy = [_format_triple(triple) for triple in triples if triple[1] == relation][:8]
    known = query["source"] if mode == "tail_prediction" else query["target"]
    supplement = [
        _format_triple(triple)
        for triple in triples
        if known in {triple[0], triple[2]} and triple[1] != relation
    ][:8]
    candidates = [item["candidate"] for item in predictions]
    question = (
        f"predict the tail entity [MASK] from ({query['source']}, {relation}, [MASK])"
        if mode == "tail_prediction"
        else f"predict the head entity [MASK] from ([MASK], {relation}, {query['target']})"
    )
    external_prompt = _load_external_kicgpt_prompt()
    return {
        "question": question,
        "candidate_answers": candidates,
        "analogy_demonstrations": analogy,
        "supplement_demonstrations": supplement,
        "final_query": (
            "Sort the candidate answers from most likely to least likely using only the "
            "KG facts and demonstrations."
        ),
        "source": "KICGPT-style in-context link prediction over the provided KG.",
        "external_prompt_loaded": bool(external_prompt),
        "external_prompt_final_query_template": external_prompt.get("final_query_template", "") if external_prompt else "",
    }


def _write_kicgpt_compatible_dataset(
    working_dir: Path,
    *,
    triples: list[Triple],
    query: Mapping[str, str],
    predictions: list[dict[str, Any]],
    mode: str,
) -> None:
    dataset_dir = working_dir / "dataset" / "kgagent_custom"
    neighbor_dir = dataset_dir / "get_neighbor"
    demonstration_dir = dataset_dir / "demonstration"
    neighbor_dir.mkdir(parents=True, exist_ok=True)
    demonstration_dir.mkdir(parents=True, exist_ok=True)

    entities = sorted(_entities(triples))
    relations = sorted({r for _, r, _ in triples} | {query["relation"]})
    ent2id = {entity: str(i) for i, entity in enumerate(entities)}
    rel2id = {relation: str(i) for i, relation in enumerate(relations)}

    (neighbor_dir / "entity2id.txt").write_text(
        "".join(f"{entity}\t{idx}\n" for entity, idx in ent2id.items()),
        encoding="utf-8",
    )
    (neighbor_dir / "relation2id.txt").write_text(
        "".join(f"{relation}\t{idx}\n" for relation, idx in rel2id.items()),
        encoding="utf-8",
    )
    (neighbor_dir / "train2id.txt").write_text(
        "".join(f"{ent2id[h]} {rel2id[r]} {ent2id[t]}\n" for h, r, t in triples),
        encoding="utf-8",
    )
    (neighbor_dir / "valid2id.txt").write_text("", encoding="utf-8")
    (neighbor_dir / "test2id.txt").write_text("", encoding="utf-8")
    (dataset_dir / "entity2text.txt").write_text(
        "".join(f"{entity}\t{entity}\n" for entity in entities),
        encoding="utf-8",
    )

    key_entity = query["source"] if mode == "tail_prediction" else query["target"]
    if key_entity not in ent2id:
        return
    key = "\t".join([ent2id[key_entity], rel2id[query["relation"]]])
    candidate_ids = [ent2id[item["candidate"]] for item in predictions if item.get("candidate") in ent2id]
    query_name = "tail" if mode == "tail_prediction" else "head"
    (dataset_dir / f"retriever_candidate_{query_name}.txt").write_text(
        json.dumps({key: candidate_ids}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    test_answer = [
        {
            "ID": "kgagent_query_0",
            "Question": query["relation"],
            "HeadEntity": query["source"],
            "Answer": query["target"],
        }
    ]
    (dataset_dir / "test_answer.txt").write_text(
        json.dumps(test_answer, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    relation_demos = [
        [h, r, t]
        for h, r, t in triples
        if r == query["relation"]
    ]
    local_demos = [
        [h, r, t]
        for h, r, t in triples
        if key_entity in {h, t}
    ]
    (demonstration_dir / f"{query_name}_analogy.txt").write_text(
        json.dumps({key: relation_demos}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (demonstration_dir / f"{query_name}_supplement.txt").write_text(
        json.dumps({key: local_demos}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (demonstration_dir / f"T_link_base_{query_name}.txt").write_text(
        json.dumps({key: relation_demos}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_external_kicgpt_prompt() -> dict[str, Any]:
    prompt_path = _project_root() / "external" / "KICGPT" / "prompts" / "link_prediction.json"
    if not prompt_path.exists():
        return {}
    try:
        prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    chat_prompt = prompt.get("chat", {})
    return chat_prompt if isinstance(chat_prompt, dict) else {}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _completion_working_dir(
    input_data: Mapping[str, Any],
    triples: list[Triple],
    workspace: str | Path | None = None,
) -> Path:
    explicit = input_data.get("working_dir")
    if explicit:
        path = Path(str(explicit)).expanduser()
        if not path.is_absolute() and workspace is not None:
            path = Path(workspace).resolve() / path
        return path
    base = Path(workspace or ".").resolve() / ".kgagent_completion" / "kicgpt"
    return base / _stable_kg_id(triples)


def _stable_kg_id(triples: list[Triple]) -> str:
    payload = json.dumps(sorted(triples), ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"kg_{digest}"


def _write_ready_marker(working_dir: Path, *, triples: list[Triple]) -> None:
    marker = {
        "method": "kicgpt",
        "triple_count": len(triples),
        "kg_id": _stable_kg_id(triples),
    }
    (working_dir / ".kgagent_kicgpt_ready.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _completion_warnings(triples: list[Triple]) -> list[str]:
    warnings: list[str] = []
    if len(triples) < 20:
        warnings.append(
            "The input KG is small; KICGPT-style completion may be unstable because "
            "there are few in-context demonstrations."
        )
    if len({r for _, r, _ in triples}) < 2:
        warnings.append("The KG has very few relation types, so relation analogy is limited.")
    return warnings


def _entities(triples: Iterable[Triple]) -> set[str]:
    entities: set[str] = set()
    for h, _, t in triples:
        entities.add(h)
        entities.add(t)
    return entities


def _clean_value(value: Any) -> str:
    return str(value or "").strip()


def _format_triple(triple: Triple) -> str:
    h, r, t = triple
    return f"({h}, {r}, {t})"


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _candidate_reason(
    mode: str,
    known_entity: str,
    candidate: str,
    relation: str,
    evidence: list[str],
) -> str:
    direction = "tail" if mode == "tail_prediction" else "head"
    if evidence:
        return (
            f"KICGPT-style {direction} prediction ranks `{candidate}` using relation "
            f"analogy for `{relation}` and local KG evidence around `{known_entity}`."
        )
    return f"KICGPT-style {direction} prediction keeps `{candidate}` as a low-evidence candidate."


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[A-Za-z0-9]+", left.lower()))
    right_tokens = set(re.findall(r"[A-Za-z0-9]+", right.lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)


def _relation_name_hint(candidate: str, relation: str) -> float:
    candidate_tokens = set(re.findall(r"[A-Za-z0-9]+", candidate.lower()))
    relation_tokens = set(re.findall(r"[A-Za-z0-9]+", relation.lower()))
    if not candidate_tokens or not relation_tokens:
        return 0.0
    return 1.0 if candidate_tokens & relation_tokens else 0.0
