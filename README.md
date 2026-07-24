# KGAgent - Knowledge Graph Extraction System

KGAgent is a unified, Claude Agent SDK-based system for knowledge graph extraction. It supports three extraction types through a natural-language interface:

- **Relation Triples** - Standard entity-relation-entity triples
- **Temporal Quadruples** - Temporal facts with time annotations
- **Hyper-relations** - Relations with structured contextual attributes

## Architecture

KGAgent follows a clean, modular architecture inspired by DataFlow-Table_SDK:

```
User Input (natural language or file)
  └─ KGAgentSystem  →  orchestrator (type detection)
       └─ ExtractionEntry  →  extraction agent (direct KG extraction)
            └─ Result (validated JSON)
```

### Directory Structure

```
src/kgagent/
├── core/              # Shared infrastructure (config, logging, I/O, validation)
├── system/            # Unified system layer (KGAgentSystem, orchestrator, registry)
├── extraction/        # Extraction route (ExtractionEntry, agents, tools)
│   ├── agents/        # Extraction agent definition
│   └── tools/         # MCP tools, loaders, validation utilities
└── api/               # CLI and chat interfaces
```

## Installation

**Requirements:** Python 3.10+

```bash
git clone <repository-url>
cd KGAgent2

# Create environment
conda create -n kgagent python=3.11 -y && conda activate kgagent
# or: python -m venv venv && source venv/bin/activate

# Install
pip install -e .
```

## Configuration

Set up your API credentials via environment variables:

```bash
export KG_API_URL="http://127.0.0.1:18080"
export KG_API_KEY="<YOUR_API_KEY>"
export KG_MODEL="claude-sonnet-4-6"  # optional, defaults to claude-sonnet-4-6
```

Or create a `config.local.py` file in the project root (gitignored):

```python
import os
os.environ["KG_API_URL"] = "http://..."
os.environ["KG_API_KEY"] = "sk-..."
os.environ["KG_MODEL"] = "deepseek-v4-flash"  # or your preferred model
```

## Usage

### Command-Line Interface

**Interactive Chat:**
```bash
kgagent chat
```

**Direct Extraction:**
```bash
# Extract from text
kgagent extract "Alice works at Acme Corporation" --type triples

# Extract from file
kgagent extract examples/test_data.json --type hyper --output result.json
```

**Batch Extraction:**
```bash
kgagent batch batch_config.json --concurrency 4
```

### Python API

```python
from kgagent import KGAgentSystem

system = KGAgentSystem()

# Extract from text
result = system.extract(
    "Alice works at Acme Corporation",
    extraction_type="triples"
)

# Extract from file with validation
result = system.extract(
    "data.json",
    extraction_type="hyper",
    validate=True,
    save_to="output.json"
)
```

## Extraction Types

| Type | Keywords | Output Format |
|------|----------|---------------|
| **Relation Triples** | `triples`, `relations`, `kg` | `{"entities": [...], "relations": [[s,r,o]]}` |
| **Temporal Quadruples** | `temporal`, `time`, `when` | `{"quadruples": ["<subj>X<obj>Y<rel>Z<time>T"]}` |
| **Hyper-relations** | `hyper`, `context`, `attributes` | `{"hyper_relations": ["<subj>X<obj>Y<rel>Z<attr>V"]}` |

## License

MIT License - Version 0.3.0
