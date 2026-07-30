# Format Conversion Feature

## Overview

The format conversion module allows users to convert knowledge graph extraction results into various formats suitable for different graph databases and tools.

## Supported Formats

### 1. Neo4j CSV Format
- **Format codes**: `neo4j`, `neo4j_csv`, `csv`
- **Output**: Two CSV files (nodes.csv and relationships.csv)
- **Use case**: Import into Neo4j using `LOAD CSV` or `neo4j-admin import`

### 2. RDF Format (Coming Soon)
- **Format codes**: `rdf`, `turtle`, `ttl`
- **Output**: RDF/Turtle file
- **Use case**: Semantic web applications, triple stores

### 3. GraphML Format (Coming Soon)
- **Format codes**: `graphml`, `xml`
- **Output**: GraphML XML file
- **Use case**: Graph visualization tools (Gephi, yEd)

### 4. JSON Format
- **Format codes**: `json`
- **Output**: Structured JSON file
- **Use case**: Custom processing, data exchange

## Usage

### Command Line (Coming Soon)
```bash
# Convert extraction result to Neo4j CSV
kgagent convert --input result.json --format neo4j --output ./neo4j_import

# Convert with format auto-detection
kgagent convert --input result.json --format neo4j_csv
```

### Chat Interface

After performing an extraction, use natural language to convert:

**English:**
- "convert to neo4j format"
- "save as neo4j csv"
- "export to neo4j"
- "convert the last result to graphml"

**Chinese:**
- "转换成neo4j格式"
- "保存为neo4j csv"
- "把结果转换成RDF格式"
- "导出为graphml"

**With custom output path:**
- "save as neo4j format to /path/to/output"
- "转换成neo4j格式，保存到 /Users/zhp_li/neo4j/import"

### Python API

```python
from kgagent.conversion import ConversionEntry

converter = ConversionEntry()

# Convert extraction result
result = converter.convert(
    input_data=extraction_result,  # Dict or list of dicts
    output_format="neo4j_csv",
    output_path="./output",
)

print(f"Nodes: {result['nodes_file']}")
print(f"Relationships: {result['relationships_file']}")
print(f"Statistics: {result['statistics']}")
```

### Orchestrator Integration

```python
from kgagent.system.orchestrator import run_conversion

# Convert the last extraction result
result = await run_conversion(
    source="last",
    target_format="neo4j_csv",
    output_path="./output",
    last_result=last_extraction_result,
)

# Convert from a file
result = await run_conversion(
    source="file:/path/to/data.json",
    target_format="neo4j_csv",
    output_path="./output",
)
```

## Neo4j CSV Format Details

### Input Format
The converter expects KG data in tagged format:
```json
{
  "text": "Alice works at Acme Corporation",
  "kg": [
    "<subj> Alice <obj> Acme Corporation <rel> works_at",
    "<subj> Alice <obj> New York <rel> works_in"
  ]
}
```

Or with additional attributes:
```json
{
  "kg": [
    "<subj> Bob <obj> Tech Corp <rel> founded <time> 2020",
    "<subj> Alice <obj> Project <rel> manages <location> New York"
  ]
}
```

### Output Format

**nodes.csv:**
```csv
entityId:ID,name:STRING,:LABEL
0,Alice,Entity
1,Acme Corporation,Entity
2,New York,Entity
```

**relationships.csv:**
```csv
:START_ID,:END_ID,relation:STRING,:TYPE,time:STRING,location:STRING
0,1,works_at,RELATION,,
0,2,works_in,RELATION,,
0,3,manages,RELATION,,New York
```

### Import to Neo4j

```bash
# Using neo4j-admin import (for empty database)
neo4j-admin database import full \
  --nodes=import/nodes.csv \
  --relationships=import/relationships.csv \
  neo4j

# Using LOAD CSV (for existing database)
LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
CREATE (n:Entity {entityId: toInteger(row.entityId), name: row.name});

LOAD CSV WITH HEADERS FROM 'file:///relationships.csv' AS row
MATCH (a:Entity {entityId: toInteger(row.`:START_ID`)})
MATCH (b:Entity {entityId: toInteger(row.`:END_ID`)})
CREATE (a)-[r:RELATION {relation: row.`relation:STRING`}]->(b);
```

## Intent Classification

The intent classifier automatically detects conversion requests:

**Patterns recognized:**
- "convert [to/into] <format>"
- "save as <format>"
- "export [to/as] <format>"
- "转换成 <format>"
- "保存为 <format>"
- "导出为 <format>"

**Intent output:**
```json
{
  "intent": "convert",
  "confidence": 0.95,
  "parameters": {
    "source": "last",
    "target_format": "neo4j_csv",
    "output_path": null
  },
  "explanation": "User wants to convert the last result to Neo4j CSV format"
}
```

## Architecture

```
kgagent/conversion/
├── __init__.py          # Module exports
├── entry.py             # ConversionEntry (main interface)
├── neo4j_csv.py         # Neo4j CSV converter
├── rdf.py               # RDF converter (placeholder)
└── graphml.py           # GraphML converter (placeholder)
```

**Key components:**

1. **ConversionEntry**: Main conversion interface
   - Format normalization and routing
   - Input validation (file or data)
   - Error handling

2. **Neo4j CSV Converter**: 
   - Parses tagged KG format
   - Generates entity-to-ID mapping
   - Creates CSV files with proper headers
   - Handles additional attributes dynamically

3. **Orchestrator Integration**:
   - `run_conversion()` function
   - Resolves source data (last result or file)
   - Passes data to converter

4. **Chat Integration**:
   - Intent classification for convert requests
   - User-friendly output messages
   - Automatic format detection

## Testing

Run the test suite:

```bash
# Test Neo4j CSV conversion
python test_conversion.py

# Test orchestrator integration
python test_integration.py
```

## Future Enhancements

1. **RDF Converter**: Support Turtle, N-Triples, N3 formats
2. **GraphML Converter**: XML format for graph visualization
3. **Custom schemas**: User-defined node/relationship types
4. **Batch conversion**: Convert multiple files at once
5. **Validation**: Verify converted data integrity
6. **CLI integration**: Direct command-line conversion tool
