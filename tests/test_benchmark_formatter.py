from kgagent.benchmark.formatter import format_benchmark


def test_temporal_benchmark_with_facts_is_valid():
    result = format_benchmark(
        [
            {
                "sample_id": "t1",
                "generated_question": "When did Alice work at Acme?",
                "answer": {"text": "2020", "type": "time", "id": "TF1"},
                "subgraph": {
                    "facts": [
                        {
                            "id": "TF1",
                            "subject": "Alice",
                            "relation": "works_at",
                            "object": "Acme",
                            "time": {"type": "point", "value": "2020"},
                        }
                    ]
                },
                "graph_type": "TKG",
                "constraints": {"hop": 1, "difficulty": "easy"},
                "source": {"graph_id": "demo"},
                "method": "sgsh_prompt",
            }
        ],
        benchmark_type="temporal_KGQA",
    )

    assert result["stats"]["total"] == 1
    assert result["stats"]["valid"] == 1
