from kgagent.benchmark.tools.formatter import format_benchmark


def test_temporal_benchmark_with_facts_is_valid():
    result = format_benchmark(
        [
            {
                "sample_id": "t1",
                "generated_question": "When did Alice work at Acme?",
                "answer": {"text": "2020", "type": "time", "id": "TF1"},
                "subgraph": {
                    "nodes": [
                        {"id": "Alice", "name": "Alice", "type": "Entity"},
                        {"id": "Acme", "name": "Acme", "type": "Entity"},
                    ],
                    "edges": [
                        {
                            "source": "Alice",
                            "relation": "works_at",
                            "target": "Acme",
                            "time": {"type": "point", "value": "2020"},
                        }
                    ],
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


def test_chinese_question_mark_is_valid():
    result = format_benchmark(
        [
            {
                "sample_id": "zh1",
                "generated_question": "谁创办了阿克米机器人公司？",
                "answer": {"text": "陈爱丽丝", "type": "entity", "id": "E1"},
                "subgraph": {
                    "nodes": [
                        {"id": "E1", "name": "陈爱丽丝", "type": "人物"},
                        {"id": "E2", "name": "阿克米机器人公司", "type": "组织"},
                    ],
                    "edges": [{"source": "E1", "relation": "创办", "target": "E2"}],
                },
                "graph_type": "KG",
                "constraints": {"hop": 1, "difficulty": "easy"},
                "source": {"graph_id": "demo"},
                "method": "sgsh_prompt",
            }
        ],
        benchmark_type="KGQA",
    )

    assert result["stats"]["valid"] == 1
