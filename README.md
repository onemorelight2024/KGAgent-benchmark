# KGAgent2 - Knowledge Graph Agent System

KGAgent2 is a modular, Claude Agent SDK-based system for comprehensive knowledge graph operations. Currently focused on **knowledge graph extraction**, with architecture designed to support future capabilities like reasoning, querying, fusion, and visualization.

## Current Features

### Knowledge Graph Extraction

Four extraction types with natural-language interface:

- **Relation Triples** - Standard entity-relation-entity triples `(subject, relation, object)`
- **Temporal Quadruples** - Temporal facts with time annotations `(subject, relation, object, time)`
- **Hyper-relations** - Relations with structured contextual attributes
- **Event KG** - AutoSchemaKG three-stage event extraction (entity-relations, event-entities, event-relations)

### Interactive Chat Interface

- ✅ Arrow key navigation and command history (powered by `prompt_toolkit`)
- ✅ Ctrl+C to cancel current task without exiting chat
- ✅ Auto-detection of extraction type from keywords
- ✅ Batch processing with concurrency control
- ✅ File input/output with automatic result saving

---

## Architecture

KGAgent2 follows a **modular route-based architecture** where each major capability (extraction, reasoning, etc.) is a self-contained route with its own agents and tools.

```
┌─────────────────────────────────────────────────────────────┐
│                       User Interface                        │
│  • CLI (kgagent extract/batch)                             │
│  • Interactive Chat (kgagent chat)                         │
│  • Python API (KGAgentSystem)                              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    KGAgentSystem (Unified Entry)            │
│  • Route detection and dispatching                          │
│  • Configuration management                                 │
│  • Batch processing orchestration                           │
└────────────────────────┬────────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    ┌─────────┐    ┌─────────┐    ┌──────────┐
    │Extract  │    │Reasoning│    │  Query   │
    │ Route   │    │  Route  │    │  Route   │
    │         │    │(future) │    │ (future) │
    └────┬────┘    └─────────┘    └──────────┘
         │
         ├─ Orchestrator (type detection, routing)
         │
         ├─ Registry (extraction types, keywords)
         │
         ├─ ExtractionEntry / EventExtractionEntry
         │   └─ Claude Agent SDK agents
         │       └─ Prompts + Schema validation
         │
         └─ Tools
             ├─ Loaders (JSON, file I/O)
             ├─ Validators (JSON Schema)
             └─ AutoSchemaKG (event extraction)
```

### Core Components

#### 1. **System Layer** (`src/kgagent/system/`)
- **KGAgentSystem**: Unified entry point for all operations
- **Orchestrator**: Route detection and task dispatching
- **Registry**: Type registration and keyword-based detection

#### 2. **Extraction Route** (`src/kgagent/extraction/`)
- **Entry Points**: `kg_entry.py` (triples/temporal/hyper), `event_entry.py` (AutoSchemaKG)
- **Agents**: Claude Agent SDK definitions with extraction prompts
- **Tools**: Loaders, validators, schema utilities
- **Prompts**: `src/kgagent/prompts/*.md` (versioned extraction instructions)
- **Schemas**: `src/kgagent/schemas/*.json` (JSON Schema validation)

#### 3. **API Layer** (`src/kgagent/api/`)
- **CLI**: `kgagent extract/batch/chat` commands
- **Chat**: Interactive terminal with history and cancellation support

#### 4. **Core Infrastructure** (`src/kgagent/core/`)
- **Config**: Environment variables and settings
- **Validators**: JSON Schema validation for all extraction types
- **I/O**: File operations and result recording

---

## Installation

**Requirements:** Python 3.10+

```bash
git clone <repository-url>
cd KGAgent2

# Create environment
conda create -n kgagent python=3.10 -y && conda activate kgagent
# or: python -m venv venv && source venv/bin/activate

# Install in development mode
pip install -e .
```

### Dependencies

- `claude-agent-sdk>=0.2.0` - Claude Agent SDK for LLM orchestration
- `jsonschema>=4.0.0` - JSON Schema validation
- `prompt_toolkit>=3.0.0` - Rich terminal interface
- `anyio>=4.0.0` - Async utilities

---

## Configuration

### API Credentials

Set up your LLM API via environment variables:

```bash
# For Anthropic Claude
export ANTHROPIC_API_KEY="sk-ant-..."
export ANTHROPIC_MODEL="claude-sonnet-4-6"

# For OpenAI-compatible APIs (e.g., DeepSeek, custom proxy)
export OPENAI_API_BASE="http://123.129.219.111:3000/v1"
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_MODEL="deepseek-v4-flash"
```

### Claude Code Router (Optional)

For routing requests through a proxy or using multiple models:

```bash
mkdir -p ~/.claude-code-router
cat > ~/.claude-code-router/config.json <<'EOF'
{
  "APIKEY": "sk-ccr",
  "API_TIMEOUT_MS": 1200000,
  "Providers": [
    {
      "name": "myopenai",
      "api_base_url": "http://your-api-base/v1/chat/completions",
      "api_key": "$OPENAI_API_KEY",
      "models": ["deepseek-v4-flash", "gpt-5.4"]
    }
  ],
  "Router": {
    "default": "myopenai,deepseek-v4-flash"
  }
}
EOF
```

---

## Usage

### Interactive Chat (Recommended)

```bash
kgagent chat
```

Features:
- Type extraction requests in natural language
- Load files directly: `examples/test_data.json --type event`
- Use arrow keys to navigate history
- Press **Ctrl+C** to cancel current task (stay in chat)
- Type `:help` for commands, `:quit` to exit

### Command-Line Interface

**Direct Extraction:**
```bash
# Extract from text
kgagent extract "Alice works at Acme Corporation" --type triples

# Extract from file
kgagent extract examples/test_data.json --type event --output result.json
```

**Batch Extraction:**
```bash
# Process multiple items concurrently
kgagent batch batch_config.json --concurrency 4
```

Batch config format:
```json
{
  "tasks": [
    {"data": "text to extract", "extraction_type": "triples"},
    {"data": {"text": "..."}, "extraction_type": "temporal"}
  ]
}
```

### Python API

```python
from kgagent import KGAgentSystem

# Initialize system
system = KGAgentSystem(
    model_name="deepseek-v4-flash",  # optional
    work_dir="./tmp_sdk"              # optional
)

# Single extraction
result = system.extract(
    data="Alice works at Acme Corporation",
    extraction_type="triples"
)

# Batch extraction (async)
import asyncio

tasks = [
    {"data": "text 1", "extraction_type": "triples"},
    {"data": "text 2", "extraction_type": "event"}
]

results = asyncio.run(
    system.extract_batch(tasks, max_concurrency=4)
)
```

---

## Extraction Types

| Type | Keywords | Output Format | Example |
|------|----------|---------------|---------|
| **Relation Triples** | `triples`, `relations`, `kg` | `{"entities": [...], "relations": [[s,r,o]]}` | Standard KG |
| **Temporal Quadruples** | `temporal`, `time`, `when`, `timeline` | `{"quadruples": ["<subj>X<obj>Y<rel>Z<time>T"]}` | Historical events |
| **Hyper-relations** | `hyper`, `context`, `attributes` | `{"hyper_relations": ["<subj>X<obj>Y<rel>Z<attr>V"]}` | Rich contextual KG |
| **Event KG** | `event`, `autoschema`, `happening` | `{"entity_relation_dict": [...], "event_entity_relation_dict": [...], "event_relation_dict": [...]}` | AutoSchemaKG 3-stage |

### Output File Naming

Results are saved to the input file's directory with format:
```
<original_filename>_<extraction_type>_kg.json
```

Examples:
- `data.json` + `triples` → `data_triples_kg.json`
- `events.json` + `event` → `events_event_kg.json`

---

## Developer Guide: Adding New Capabilities

KGAgent2 is designed as a **comprehensive KG platform**. Here's how to extend it with new routes (reasoning, query, fusion, etc.):

### Architecture Principles

1. **Route-based modularity**: Each capability is a self-contained route
2. **Registry pattern**: Routes register with the system via keywords/types
3. **Agent-first**: Routes use Claude Agent SDK for LLM orchestration
4. **Schema validation**: All outputs follow versioned JSON schemas
5. **Async by default**: All routes support batch processing

### Step-by-Step: Adding a New Route

Let's walk through adding a **Reasoning Route** as an example.

#### Step 1: Create Route Directory Structure

```bash
mkdir -p src/kgagent/reasoning/{agents,tools}
touch src/kgagent/reasoning/__init__.py
touch src/kgagent/reasoning/reasoning_entry.py
touch src/kgagent/reasoning/agents/reasoner.py
```

#### Step 2: Define the Agent

**`src/kgagent/reasoning/agents/reasoner.py`**

```python
from claude_agent_sdk import AgentDefinition
from kgagent.system.prompts import load_prompt

def build_reasoning_agent() -> AgentDefinition:
    """Build reasoning agent definition."""
    prompt = load_prompt("reasoning")  # Load from prompts/reasoning.md
    
    return AgentDefinition(
        name="ReasoningAgent",
        prompt=prompt,
        tools=[],  # Add MCP tools if needed
    )
```

#### Step 3: Create Entry Point

**`src/kgagent/reasoning/reasoning_entry.py`**

```python
from __future__ import annotations

import asyncio
import logging
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from kgagent.extraction.config import ExtractionConfig
from kgagent.reasoning.agents.reasoner import build_reasoning_agent

logger = logging.getLogger(__name__)

class ReasoningEntry:
    """Entry point for reasoning route."""
    
    def __init__(self, config: ExtractionConfig):
        self.config = config
        self.agent = build_reasoning_agent()
    
    async def reason_async(
        self,
        kg_data: dict[str, Any],
        reasoning_type: str = "inductive",
    ) -> dict[str, Any]:
        """Run reasoning on KG data.
        
        Args:
            kg_data: Input knowledge graph
            reasoning_type: Type of reasoning (inductive, deductive, abductive)
        
        Returns:
            Reasoning result
        """
        logger.info(f"Starting reasoning: type={reasoning_type}")
        
        # Build prompt
        prompt = self._build_prompt(kg_data, reasoning_type)
        
        # Run agent
        options = ClaudeAgentOptions(
            cwd=self.config.work_dir,
            model=self.config.model_name,
            system_prompt=self.agent.prompt,
            tools=self.agent.tools,
            permission_mode=self.config.permission_mode,
            max_turns=self.config.max_turns,
        )
        
        result = None
        async with ClaudeSDKClient(options) as client:
            await client.query(prompt)
            # Parse response...
            result = {"reasoning": "result"}
        
        logger.info("Reasoning complete")
        return result
    
    def _build_prompt(self, kg_data: dict, reasoning_type: str) -> str:
        """Build reasoning prompt."""
        import json
        return f"Perform {reasoning_type} reasoning on:\n{json.dumps(kg_data, indent=2)}"
```

#### Step 4: Register with System

**Update `src/kgagent/system/registry.py`**

```python
# Add reasoning types
self.register_type(
    "reasoning",
    keywords=["reason", "reasoning", "infer", "inference", "conclude"],
    description="Reasoning over knowledge graphs"
)
```

**Update `src/kgagent/system/orchestrator.py`**

```python
# Add routing logic
if extraction_type == "reasoning":
    from kgagent.reasoning.reasoning_entry import ReasoningEntry
    
    logger.info(f"Running {extraction_type} reasoning")
    reasoning_entry = ReasoningEntry(config)
    result = await reasoning_entry.reason_async(processed_data)
else:
    # existing extraction routes...
```

#### Step 5: Add Prompt and Schema

**`src/kgagent/prompts/reasoning.md`**

```markdown
# Reasoning Agent

You are a reasoning agent for knowledge graphs.

## Your Task

Given a knowledge graph, perform logical reasoning to infer new facts...

## Output Format

Return JSON:
```json
{
  "inferred_facts": [
    {"subject": "X", "relation": "R", "object": "Y", "confidence": 0.95}
  ],
  "reasoning_chain": ["step1", "step2", "step3"]
}
```
```

**`src/kgagent/schemas/reasoning.schema.json`**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Reasoning Result",
  "type": "object",
  "required": ["inferred_facts"],
  "properties": {
    "inferred_facts": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["subject", "relation", "object"],
        "properties": {
          "subject": {"type": "string"},
          "relation": {"type": "string"},
          "object": {"type": "string"},
          "confidence": {"type": "number"}
        }
      }
    }
  }
}
```

#### Step 6: Add Validator

**Update `src/kgagent/core/validators.py`**

```python
def validate_reasoning_result(result: Any) -> dict[str, Any]:
    """Validate reasoning result."""
    errors = []
    
    if not isinstance(result, dict):
        return {"valid": False, "errors": ["Result must be a dictionary"]}
    
    if JSONSCHEMA_AVAILABLE:
        try:
            schema = _load_schema("reasoning")
            validate(instance=result, schema=schema)
        except ValidationError as e:
            errors.append(f"Schema validation failed: {e.message}")
    
    # Manual validation...
    inferred_facts = result.get("inferred_facts", [])
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "fact_count": len(inferred_facts)
    }
```

#### Step 7: Add CLI Command

**Update `src/kgagent/api/cli.py`**

```python
# Add reasoning subcommand
reason_parser = subparsers.add_parser("reason", help="Reason over KG")
reason_parser.add_argument("kg_file", help="Input KG JSON file")
reason_parser.add_argument("--type", choices=["inductive", "deductive", "abductive"], default="inductive")
reason_parser.add_argument("--output", "-o", help="Output file")

# Add handler
elif args.command == "reason":
    with open(args.kg_file) as f:
        kg_data = json.load(f)
    
    system = KGAgentSystem(work_dir=args.workspace or "./tmp_sdk")
    result = asyncio.run(system.reason_async(kg_data, args.type))
    
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
    else:
        print(json.dumps(result, indent=2))
```

#### Step 8: Add Tests

**`tests/test_reasoning.py`**

```python
import pytest
from kgagent import KGAgentSystem

@pytest.mark.asyncio
async def test_reasoning():
    system = KGAgentSystem()
    
    kg_data = {
        "relations": [
            ["Alice", "works_at", "Acme"],
            ["Acme", "located_in", "NYC"]
        ]
    }
    
    result = await system.reason_async(kg_data, reasoning_type="inductive")
    
    assert "inferred_facts" in result
    assert isinstance(result["inferred_facts"], list)
```

---

### Best Practices for New Routes

1. **Self-contained routes**: Each route should be independent with its own agents, tools, prompts, schemas
2. **Consistent naming**: Follow the pattern `<route>_entry.py`, `<route>_agent.py`
3. **Schema-first design**: Define JSON schema before implementing the route
4. **Async by default**: All entry points should have `async` versions for batch processing
5. **Logging**: Use `logger.info()` for key operations, `logger.warning()` for errors
6. **Error handling**: Wrap in try-except, return `{"error": "..."}` on failure
7. **Validation**: Always validate outputs with JSON Schema + manual checks
8. **Documentation**: Update README with new route details and usage examples

### Route Ideas for Future Development

- **Reasoning**: Inductive/deductive/abductive reasoning over KG
- **Query**: SPARQL-like natural language queries
- **Fusion**: Merge multiple KGs with entity alignment
- **Completion**: Predict missing links/entities
- **Visualization**: Generate graph visualizations (GraphViz, D3.js)
- **Explanation**: Generate natural language explanations from KG
- **Validation**: Consistency checking, constraint validation
- **Evolution**: Track KG changes over time, version management

---

## Project Structure

```
KGAgent2/
├── src/kgagent/
│   ├── core/                    # Core infrastructure
│   │   ├── config.py            # Configuration
│   │   └── validators.py        # JSON Schema validators
│   │
│   ├── system/                  # System layer
│   │   ├── system.py            # KGAgentSystem (main entry)
│   │   ├── orchestrator.py      # Route orchestrator
│   │   ├── registry.py          # Type registry
│   │   └── prompts.py           # Prompt loader
│   │
│   ├── extraction/              # Extraction route
│   │   ├── kg_entry.py          # Entry for triples/temporal/hyper
│   │   ├── event_entry.py       # Entry for AutoSchemaKG events
│   │   ├── config.py            # Extraction config
│   │   ├── record.py            # Result recording
│   │   ├── agents/              # Agent definitions
│   │   │   └── extractor.py
│   │   └── tools/               # Extraction tools
│   │       ├── loaders.py       # File I/O
│   │       ├── validation.py    # Validation utilities
│   │       └── autoschema.py    # AutoSchemaKG prompts
│   │
│   ├── prompts/                 # Versioned prompts
│   │   ├── extraction.md        # Main extraction prompt
│   │   └── event_extraction.md  # Event extraction prompt
│   │
│   ├── schemas/                 # JSON Schemas
│   │   ├── relation_triples.schema.json
│   │   ├── temporal_quadruples.schema.json
│   │   ├── hyper_relations.schema.json
│   │   └── autoschema_kg.schema.json
│   │
│   └── api/                     # User interfaces
│       ├── cli.py               # CLI commands
│       └── chat.py              # Interactive chat
│
├── tests/                       # Test suite
├── examples/                    # Example data files
├── pyproject.toml               # Project config
└── README.md                    # This file
```

---

## Contributing

Contributions are welcome! Whether you're adding new routes, improving extraction accuracy, or enhancing the developer experience:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/reasoning-route`
3. Follow the architecture patterns outlined above
4. Add tests for new functionality
5. Update documentation (README, docstrings)
6. Submit a pull request

---

## Troubleshooting

### Empty Extraction Results (`"kg": []`)

If batch extraction produces empty results:
- Check logs for `"error"` field in result JSON
- Common causes: LLM output format not parsable, text too long/complex
- Solutions: Increase `max_turns`, simplify text, add retry logic

### Event Loop Conflicts

If you see `RuntimeError: asyncio.run() cannot be called from a running event loop`:
- Use `async`/`await` versions of functions (`extract_async`, `prompt_async`)
- Don't call `asyncio.run()` inside async functions

### Module Not Found

If imports fail after adding new routes:
- Ensure `__init__.py` exists in all directories
- Run `pip install -e .` to reinstall in editable mode

---

## License

MIT License - Version 0.3.0

## Acknowledgments

- Built with [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk)
- Event extraction inspired by [AutoSchemaKG](https://github.com/DILAB-HYU/AutoSchemaKG)
- Architecture influenced by DataFlow-Table_SDK design patterns
