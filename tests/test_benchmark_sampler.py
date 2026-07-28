from kgagent.benchmark.subgraph_sampler import _build_adjacency, _extract_subgraph_from_path, _random_walk, sample_subgraphs


def _tiny_kg():
    return {
        "graph_id": "kg_001",
        "graph_type": "KG",
        "entities": [
            {"id": "E1", "name": "Alice Chen", "type": "Person"},
            {"id": "E2", "name": "Acme Robotics", "type": "Organization"},
            {"id": "E3", "name": "Bob Smith", "type": "Person"},
            {"id": "E4", "name": "TechVentures", "type": "Organization"},
            {"id": "E5", "name": "San Francisco", "type": "Location"},
        ],
        "relations": [
            {"source": "E1", "relation": "founded", "target": "E2"},
            {"source": "E1", "relation": "works_at", "target": "E2"},
            {"source": "E3", "relation": "invested_in", "target": "E2"},
            {"source": "E3", "relation": "works_at", "target": "E4"},
            {"source": "E2", "relation": "located_in", "target": "E5"},
        ],
        "metadata": {},
    }


def test_random_walk_uses_actual_endpoint_for_backward_walk():
    kg = _tiny_kg()
    adj = _build_adjacency(kg)

    result = _random_walk("E2", 1, {"E2": [adj["E2"][0]]})

    assert result is not None
    path, answer_entity_id = result
    assert path == [("E1", "founded", "E2")]
    assert answer_entity_id == "E1"

    _, answer = _extract_subgraph_from_path(kg, path, answer_entity_id)
    assert answer["id"] == "E1"
    assert answer["name"] == "Alice Chen"


def test_sample_subgraphs_does_not_repeat_edges_inside_one_sample():
    result = sample_subgraphs(_tiny_kg(), sample_count=5, seed=42)

    assert result["samples"]
    for sample in result["samples"]:
        edges = [
            (edge["source"], edge["relation"], edge["target"])
            for edge in sample["subgraph"]["edges"]
        ]
        assert len(edges) == len(set(edges))
        assert sample["constraints"]["hop"] == len(edges)
