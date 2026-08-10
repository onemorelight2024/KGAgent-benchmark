from __future__ import annotations

COMPLETION_METHODS: list[dict[str, object]] = [
    {
        "name": "kicgpt",
        "display_name": "KICGPT",
        "task_types": ["kg_completion", "link_prediction"],
        "input_types": ["kg_triples"],
        "supports": ["tail_prediction", "head_prediction"],
        "description": (
            "Training-free KICGPT-style KG completion. It builds candidate answers and "
            "in-context demonstrations from the input KG, then lets the task agent rank "
            "candidate entities for missing-head or missing-tail link prediction."
        ),
        "run_tool": "run_kicgpt_kg_completion",
    }
]
