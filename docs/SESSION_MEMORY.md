# Chat Session Memory

KGAgent2 chat now supports **conversation memory**, allowing you to reference previous extractions and save results later.

## Features

### 1. **Automatic History Tracking**
- Keeps last 10 extraction operations
- Records input data, extraction type, results, and output paths
- Automatically maintains context across conversation

### 2. **Reference Previous Results**
You can now say:
- "save that" / "保存那个"
- "save the last result" / "保存刚才的结果"
- "save to my_result.json" / "保存为 my_result.json"

### 3. **Session Statistics**
- Type `:stats` to see session statistics
- Shows total extractions, types, duration, etc.

## Usage Examples

### Example 1: Save text extraction result

```
User> extract triples: Alice works at Acme Corporation

Assistant>
{
  "entities": ["Alice", "Acme Corporation"],
  "relations": [["Alice", "works_at", "Acme Corporation"]]
}

User> save that to my_kg.json

✓ 结果已保存到: my_kg.json
```

### Example 2: Auto-generate filename

```
User> extract temporal: Bob met Carol in 2020

Assistant>
{
  "quadruples": ["<subj> Bob <obj> Carol <rel> met <time> 2020"]
}

User> save that

✓ 结果已保存到: kg_result_20260724_182230.json
```

### Example 3: Chinese conversation

```
User> 抽取三元组：张三在2020年创办了Tech公司

Assistant>
{
  "entities": ["张三", "Tech公司"],
  "relations": [["张三", "创办", "Tech公司"]]
}

User> 把刚才的结果保存为 result.json

✓ 结果已保存到: result.json
```

### Example 4: View session stats

```
User> :stats

📊 Session Statistics:
  Duration: 120 seconds
  Total extractions: 3
  By type: {'triples': 2, 'temporal': 1, 'hyper': 0, 'event': 0}
  Files processed: 1
  Text extractions: 2
```

## How It Works

### Architecture

```
ChatSession (core/session.py)
  ↓
Stores: ExtractionRecord[]
  - timestamp
  - input_type (file/text)
  - input_data
  - extraction_type
  - result
  - output_path
  ↓
Intent Agent receives context
  - Recent extraction history
  - Can resolve "last result", "previous", etc.
  ↓
User can reference and save
```

### Context Passing

When you type something, the Intent Agent receives:

```
Recent extraction history:
1. [18:20:15] triples from examples/test.json
   Saved to: examples/test_triples_kg.json
2. [18:21:03] temporal: Bob met Carol in 2020
3. [18:22:10] event from data.json
   Saved to: data_event_kg.json

---

Classify the intent of this user input:

save that to my_result.json
```

The Agent understands "that" refers to extraction #3.

## Technical Details

### Session Memory (`core/session.py`)

**ChatSession class:**
- `add_extraction()` - Record new extraction
- `get_last_extraction()` - Get most recent
- `get_last_n_extractions(n)` - Get recent N
- `build_context_summary()` - Generate context string for Agent
- `get_session_stats()` - Session statistics

**ExtractionRecord dataclass:**
- Stores all details of one extraction operation
- Includes timestamp, input, output, metadata

### Intent Agent Enhancement

**New intent type: `save`**

```json
{
  "intent": "save",
  "confidence": 0.95,
  "parameters": {
    "target": "last",
    "file_path": "result.json"
  },
  "explanation": "User wants to save previous result"
}
```

### Limitations

1. **Session-only**: Memory cleared when chat exits
2. **Last 10 extractions**: Older history is dropped
3. **No cross-session memory**: Each chat session starts fresh

## Future Enhancements

Potential improvements:
- Persistent memory across sessions
- Reference specific extractions: "save the second one"
- Compare results: "compare this with the previous extraction"
- Merge results: "merge these two results"
- Undo/redo operations

## Commands

| Command | Description |
|---------|-------------|
| `:help` | Show help |
| `:stats` | Show session statistics |
| `:quit` / `:exit` | Exit chat |
| `save that` | Save last result (auto-generate filename) |
| `save to <path>` | Save last result to specific file |

## Testing

Test the memory feature:

```bash
kgagent chat

# 1. Do an extraction
User> extract triples: Alice works at Acme

# 2. Save it
User> save that

# 3. Check stats
User> :stats
```
