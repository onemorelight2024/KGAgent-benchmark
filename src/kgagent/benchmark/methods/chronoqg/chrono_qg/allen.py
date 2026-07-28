"""
Allen temporal operator module.
ALLEN_OPERATOR_DICT and relation_allen_time_range are copied verbatim from
TimelineKGQA/TimelineKGQA/generator.py (TKGQAGenerator.relation_allen_time_range),
to avoid a heavy dependency on the full package.

The six signed-difference vector positions are:
  0: s1 - e1   (X is a point if ==0)
  1: s2 - e2   (Y is a point if ==0)
  2: s1 - s2
  3: s1 - e2
  4: e1 - s2
  5: e1 - e2
Each component is discretised to {-1, 0, 1}.
"""


# ---------------------------------------------------------------------------
# 26-class Allen operator dictionary (source: TimelineKGQA/generator.py)
# ---------------------------------------------------------------------------
ALLEN_OPERATOR_DICT = {
    (-1, -1, -1, -1, -1, -1): {
        "relation": "X < Y",
        "description": "X precedes Y",
        "category": "tr",
        "code": "tr-1",
        "semantic": "before",
    },
    (-1, -1, -1, -1, 0, -1): {
        "relation": "X m Y",
        "description": "X meets Y",
        "category": "tr",
        "code": "tr-2",
        "semantic": "meets",
    },
    (-1, -1, -1, -1, 1, -1): {
        "relation": "X o Y",
        "description": "X overlaps Y",
        "category": "tr",
        "code": "tr-3",
        "semantic": "during",
    },
    (-1, -1, -1, -1, 1, 0): {
        "relation": "X fi Y",
        "description": "X is finished by Y",
        "category": "tr",
        "code": "tr-4",
        "semantic": "finishedby",
    },
    (-1, -1, -1, -1, 1, 1): {
        "relation": "X di Y",
        "description": "X contains Y",
        "category": "tr",
        "code": "tr-5",
        "semantic": "during",
    },
    (-1, -1, 0, -1, 1, -1): {
        "relation": "X s Y",
        "description": "X starts Y",
        "category": "tr",
        "code": "tr-6",
        "semantic": "starts",
    },
    (-1, -1, 0, -1, 1, 0): {
        "relation": "X = Y",
        "description": "X equals Y",
        "category": "tr",
        "code": "tr-7",
        "semantic": "equal",
    },
    (-1, -1, 0, -1, 1, 1): {
        "relation": "X si Y",
        "description": "X is started by Y",
        "category": "tr",
        "code": "tr-8",
        "semantic": "startedby",
    },
    (-1, -1, 1, -1, 1, -1): {
        "relation": "X d Y",
        "description": "X during Y",
        "category": "tr",
        "code": "tr-9",
        "semantic": "during",
    },
    (-1, -1, 1, -1, 1, 0): {
        "relation": "X f Y",
        "description": "X finishes Y",
        "category": "tr",
        "code": "tr-10",
        "semantic": "finishes",
    },
    (-1, -1, 1, -1, 1, 1): {
        "relation": "X oi Y",
        "description": "X is overlapped by Y",
        "category": "tr",
        "code": "tr-11",
        "semantic": "during",
    },
    (-1, -1, 1, 0, 1, 1): {
        "relation": "X mi Y",
        "description": "X is met by Y",
        "category": "tr",
        "code": "tr-12",
        "semantic": "metby",
    },
    (-1, -1, 1, 1, 1, 1): {
        "relation": "X > Y",
        "description": "X is preceded by Y",
        "category": "tr",
        "code": "tr-13",
        "semantic": "after",
    },
    (0, -1, -1, -1, -1, -1): {
        "relation": "X < Y",
        "description": "X is before Y",
        "category": "tp&tr",
        "code": "tptr-14",
        "semantic": "before",
    },
    (0, -1, 0, -1, 0, -1): {
        "relation": "X s Y",
        "description": "X starts Y",
        "category": "tp&tr",
        "code": "tptr-15",
        "semantic": "starts",
    },
    (0, -1, 1, -1, 1, -1): {
        "relation": "X d Y",
        "description": "X during Y",
        "category": "tp&tr",
        "code": "tptr-16",
        "semantic": "during",
    },
    (0, -1, 1, 0, 1, 0): {
        "relation": "X f Y",
        "description": "X finishes Y",
        "category": "tp&tr",
        "code": "tptr-17",
        "semantic": "finishes",
    },
    (0, -1, 1, 1, 1, 1): {
        "relation": "X > Y",
        "description": "X is after Y",
        "category": "tp&tr",
        "code": "tptr-18",
        "semantic": "after",
    },
    (-1, 0, -1, -1, -1, -1): {
        "relation": "X < Y",
        "description": "X is before Y",
        "category": "tr&tp",
        "code": "trtp-19",
        "semantic": "before",
    },
    (-1, 0, -1, -1, 0, 0): {
        "relation": "X fi Y",
        "description": "X finishes Y",
        "category": "tr&tp",
        "code": "trtp-20",
        "semantic": "finishes",
    },
    (-1, 0, -1, -1, 1, 1): {
        "relation": "X di Y",
        "description": "X during Y",
        "category": "tr&tp",
        "code": "trtp-21",
        "semantic": "during",
    },
    (-1, 0, 0, 0, 1, 1): {
        "relation": "X si Y",
        "description": "X starts Y",
        "category": "tr&tp",
        "code": "trtp-22",
        "semantic": "starts",
    },
    (-1, 0, 1, 1, 1, 1): {
        "relation": "X > Y",
        "description": "X is after Y",
        "category": "tr&tp",
        "code": "trtp-23",
        "semantic": "after",
    },
    (0, 0, -1, -1, -1, -1): {
        "relation": "X < Y",
        "description": "X is before Y",
        "category": "tp",
        "code": "tp-24",
        "semantic": "before",
    },
    (0, 0, 0, 0, 0, 0): {
        "relation": "X = Y",
        "description": "X equals Y",
        "category": "tp",
        "code": "tp-25",
        "semantic": "equal",
    },
    (0, 0, 1, 1, 1, 1): {
        "relation": "X > Y",
        "description": "X is after Y",
        "category": "tp",
        "code": "tp-26",
        "semantic": "after",
    },
}

# Reverse map: code -> (tuple, entry)
ALLEN_CODE_TO_TUPLE = {v["code"]: k for k, v in ALLEN_OPERATOR_DICT.items()}


def relation_allen_time_range(
    time_range_a: list,
    time_range_b: list,
) -> dict:
    """
    Return the Allen temporal relation entry for two time ranges.

    Args:
        time_range_a: [start, end] for event X  (datetime or comparable)
        time_range_b: [start, end] for event Y

    Returns:
        dict with keys: relation, description, category, code, semantic
    """
    s1, e1 = time_range_a
    s2, e2 = time_range_b

    raw = [
        s1 - e1,
        s2 - e2,
        s1 - s2,
        s1 - e2,
        e1 - s2,
        e1 - e2,
    ]

    def _sign(v):
        if v == 0:
            return 0
        return -1 if v < 0 else 1

    key = tuple(_sign(v) for v in raw)
    if key not in ALLEN_OPERATOR_DICT:
        raise ValueError(
            f"No Allen entry for six-tuple {key} "
            f"(time_range_a={time_range_a}, time_range_b={time_range_b})"
        )
    return ALLEN_OPERATOR_DICT[key]


def compute_six_tuple(time_range_a: list, time_range_b: list) -> tuple:
    """Return the raw sign tuple (-1/0/1)^6 for the two time ranges."""
    s1, e1 = time_range_a
    s2, e2 = time_range_b
    raw = [s1 - e1, s2 - e2, s1 - s2, s1 - e2, e1 - s2, e1 - e2]
    def _sign(v):
        if v == 0:
            return 0
        return -1 if v < 0 else 1
    return tuple(_sign(v) for v in raw)
