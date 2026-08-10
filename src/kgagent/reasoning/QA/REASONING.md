# Reasoning

This document covers the optional dependencies and runtime notes for `src/kgagent/reasoning`.

## Scope

KGAgent currently supports three reasoning paths:
KGAgent currently supports two QA reasoning paths:

| Method | Input | Typical use |
|---|---|---|
| `rag_anything` | PDF, Office, image-heavy, multimodal, or plain text documents | Document QA |
| `graphrag` | `.txt`, `.md`, or text folders | Document QA |

## Install

Base KGAgent:

```bash
pip install -e .
```

Reasoning extras:

```bash
pip install -e ".[reasoning]"
```

Or install from the separate file:

```bash
pip install -r requirements-reasoning.txt
```

## Extra notes

- `RAG-Anything` may need broader parser dependencies for PDF or Office files:

```bash
pip install "raganything[all]"
```

- `GraphRAG` must provide the `graphrag` CLI on `PATH`.
- Local embeddings use `sentence-transformers`.
- If you use an OpenAI-compatible gateway, make sure your chat and embedding models are both available.

## Runtime behavior

Reasoning storage is written under:

```text
.kgagent_reasoning/
.kgagent_completion/
```

These directories store reusable indices, manifests, logs, and intermediate files. They are runtime artifacts and should not be committed.

## Method selection

- Prefer `rag_anything` for PDF, scanned, image-heavy, table-heavy, or multimodal documents.
- Prefer `graphrag` for plain text and markdown corpora.

If the user does not explicitly choose a method, KGAgent recommends one based on the input modality and can ask for confirmation when both are plausible.

## Common issues

### RAG-Anything parser download failures

If MinerU tries to download models from Hugging Face and the environment cannot reach it:

- pre-download the MinerU assets locally, or
- switch to plain-text input and run `rag_anything` in `text_only` mode, or
- use `graphrag` for `.txt` / `.md` inputs.

### GraphRAG CLI compatibility

KGAgent calls the installed `graphrag` CLI directly. Different GraphRAG versions may change command-line options, so use a tested local version if you want stable behavior.

