# Output Process Module

This module handles all output formatting and recording for KGAgent2 extraction results.

## Structure

```
output_process/
├── __init__.py          # Module exports
├── formatters.py        # Format converters for different extraction types
├── record.py            # Result recording and file saving
└── README.md            # This file
```

## Components

### 1. Formatters (`formatters.py`)

Standardize extraction results into consistent formats.

**Functions:**

- `format_triple_result(result)` - Format relation triples
- `format_temporal_result(result)` - Format temporal quadruples
- `format_hyper_result(result)` - Format hyper-relations
- `format_event_result(result)` - Format event KG (AutoSchemaKG)

**Example:**

```python
from kgagent.output_process import format_triple_result

raw_result = {
    "entities": ["Alice", "Acme"],
    "relations": [
        ["Alice", "works_at", "Acme"]
    ]
}

formatted = format_triple_result(raw_result)
# {
#     "type": "triples",
#     "entities": ["Alice", "Acme"],
#     "relations": [
#         {"subject": "Alice", "relation": "works_at", "object": "Acme"}
#     ]
# }
```

### 2. Record (`record.py`)

Save extraction results to files with proper naming and metadata.

**Functions:**

- `record_result(result, file_path, ...)` - Save single extraction result
- `record_batch_results(results, file_path, ...)` - Save batch extraction results

**File Naming Convention:**

```
<original_name>_<extraction_type>_kg.json
```

Examples:
- `data.json` + `triples` → `data_triples_kg.json`
- `events.txt` + `event` → `events_event_kg.json`

**Example:**

```python
from kgagent.output_process import record_result

result = {
    "entities": ["Alice", "Acme"],
    "relations": [["Alice", "works_at", "Acme"]]
}

output_path = record_result(
    result,
    "data.json",
    extraction_type="triples"
)
# Saves to: data_triples_kg.json
```

## Output Formats

### Relation Triples

```json
{
  "type": "triples",
  "entities": ["Alice", "Acme Corporation"],
  "relations": [
    {
      "subject": "Alice",
      "relation": "works_at",
      "object": "Acme Corporation"
    }
  ]
}
```

### Temporal Quadruples

```json
{
  "type": "temporal",
  "quadruples": [
    {
      "subject": "Alice",
      "relation": "joined",
      "object": "Acme Corporation",
      "time": "2020-01",
      "raw": "<subj> Alice <obj> Acme Corporation <rel> joined <time> 2020-01"
    }
  ]
}
```

### Hyper-relations

```json
{
  "type": "hyper",
  "hyper_relations": [
    {
      "subject": "Alice",
      "relation": "joined",
      "object": "Acme Corporation",
      "attributes": {
        "time": "2020-01",
        "location": "New York",
        "position": "Engineer"
      },
      "raw": "<subj> Alice <obj> Acme <rel> joined <time> 2020-01 <location> New York <position> Engineer"
    }
  ]
}
```

### Event KG (AutoSchemaKG)

```json
{
  "type": "event",
  "entity_relations": [
    {"Head": "Alice", "Relation": "works_at", "Tail": "Acme"}
  ],
  "event_entities": [
    {"Event": "Alice joined the company", "Entity": ["Alice", "Acme"]}
  ],
  "event_relations": [
    {"Head": "Event1", "Relation": "before", "Tail": "Event2"}
  ]
}
```

## Design Principles

1. **Separation of Concerns**: Output formatting is separate from extraction logic
2. **Extensibility**: Easy to add new formatters for new extraction types
3. **Consistency**: All output follows standardized schemas
4. **Traceability**: Original raw output preserved in `"raw"` field when applicable
5. **Type Safety**: Each formatter validates and normalizes its specific format

## Adding New Formatters

To add a new extraction type formatter:

1. **Create formatter function in `formatters.py`:**

```python
def format_custom_result(result: dict[str, Any]) -> dict[str, Any]:
    """Format custom extraction result."""
    formatted = {
        "type": "custom",
        "data": [],
    }
    
    # Parse and format result...
    
    return formatted
```

2. **Export in `__init__.py`:**

```python
from kgagent.output_process.formatters import format_custom_result

__all__ = [..., "format_custom_result"]
```

3. **Update `record.py` to use the formatter** (if needed)

4. **Document the format** in this README

## Usage in Routes

Extraction routes should use these formatters before returning results:

```python
from kgagent.output_process import format_triple_result, record_result

# In extraction route
raw_result = await agent.extract(...)

# Format
formatted_result = format_triple_result(raw_result)

# Record to file
output_path = record_result(formatted_result, input_file, extraction_type="triples")

return formatted_result
```

## Testing

Test formatters with edge cases:

```python
# Test with empty result
assert format_triple_result({}) == {"type": "triples", "entities": [], "relations": []}

# Test with different input formats
assert format_triple_result({"relations": [["A", "r", "B"]]})
assert format_triple_result({"triples": [["A", "r", "B"]]})

# Test parsing tagged formats
result = format_temporal_result({"quadruples": ["<subj> A <obj> B <rel> r <time> 2020"]})
assert result["quadruples"][0]["subject"] == "A"
```
