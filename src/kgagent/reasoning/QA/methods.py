REASONING_METHODS = [
    {
        "name": "rag_anything",
        "description": "Document QA with multimodal support via RAG-Anything.",
        "task_types": ["qa_reasoning", "document_qa", "multimodal_qa"],
        "input_types": ["pdf", "office", "image", "text", "markdown", "multimodal_document"],
        "supports_persistent_storage": True,
        "run_tool": "run_rag_anything_qa",
    },
    {
        "name": "graphrag",
        "description": "Graph-based QA for text-heavy corpora and existing graph/RAG storage.",
        "task_types": ["qa_reasoning", "document_qa", "graph_qa"],
        "input_types": ["text", "markdown", "folder", "existing_storage"],
        "supports_persistent_storage": True,
        "run_tool": "run_graphrag_qa",
    },
]
