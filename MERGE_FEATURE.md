# KGAgent2 图谱合并功能说明

## 功能概述

为每个 PDF 输入生成一个完整的合并图谱，在提取完所有 chunks 后自动执行：
1. **消歧**：对于 triples、temporal、hyper 类型，合并名称不同但含义相同的实体和关系
2. **去重**：移除重复的图谱项
3. **统一输出**：生成 `*_kg_all.json` 文件

---

## 文件结构

对于每个 PDF，现在生成以下文件：

```
the_lighthouse_last_signal.pdf
├── the_lighthouse_last_signal.md              # MinerU 解析的 Markdown
├── the_lighthouse_last_signal_chunks.json     # 文本分块结果
├── the_lighthouse_last_signal_triples_kg.json # Chunk 级别的图谱
└── the_lighthouse_last_signal_triples_kg_all.json  # 合并后的完整图谱 ✨ 新增
```

---

## 合并策略

### 1. Triples / Temporal / Hyper（带消歧）

**流程**：
1. 提取所有实体和关系
2. 使用字符串相似度（阈值 0.85）找出可能相同的候选
3. 将候选发送给 LLM 判断
4. 根据 LLM 结果统一名称
5. 去重并保存

**示例**：
```
原始实体：["Mara", "Mara Ellison", "lighthouse", "Greyhaven lighthouse"]
↓ 相似度筛选
候选组：[["Mara", "Mara Ellison"], ["lighthouse", "Greyhaven lighthouse"]]
↓ LLM 判断
映射：{"Mara": "Mara Ellison", "lighthouse": "lighthouse"}
↓ 应用映射
合并后：["Mara Ellison", "lighthouse"]
```

### 2. Event（简单合并）

**流程**：
1. 保留所有 chunk 的完整数据
2. 不进行消歧（因为结构复杂）
3. 直接保存为数组

---

## API 使用

### 方法 1：使用合并函数

```python
from kgagent.extraction.merge import merge_kg_chunks
from kgagent.system import KGAgentSystem

system = KGAgentSystem()

# 合并已有的 chunk 级别图谱
result = await merge_kg_chunks(
    kg_file="path/to/file_triples_kg.json",
    extraction_type="triples",
    llm_client=system,
)

print(f"输出文件: {result['output_file']}")
print(f"合并后项数: {result['total_items']}")
print(f"实体消歧: {result['entities_merged']}")
print(f"关系消歧: {result['relations_merged']}")
```

### 方法 2：Chat 模式自动合并

在 chat 模式中，PDF 提取完成后会自动调用合并功能：

```bash
python -m kgagent.api.chat

# 输入命令
> extract triples from examples/the_lighthouse_last_signal.pdf

# 输出
[1/5] 解析 PDF...
[2/5] 切分文本...
[3/5] 提取知识图谱...
[4/5] 提取完成!
[5/5] 合并图谱...
✓ 图谱合并完成
  - 合并后图谱项: 88
  - 实体: 76 (消歧: 1)
  - 关系: 68 (消歧: 0)
  - 输出文件: the_lighthouse_last_signal_triples_kg_all.json

💾 已保存文件:
  - Chunks: the_lighthouse_last_signal_chunks.json
  - 知识图谱 (chunks): the_lighthouse_last_signal_triples_kg.json
  - 知识图谱 (合并): the_lighthouse_last_signal_triples_kg_all.json
```

---

## 输出格式

### Triples/Temporal/Hyper 合并结果

```json
{
  "extraction_type": "triples",
  "source_file": "path/to/file_triples_kg.json",
  "chunks": 4,
  "kg": [
    "<subj> Mara Ellison <obj> Thomas Reed <rel> found",
    "<subj> North Star <obj> harbor <rel> is_east_of",
    ...
  ],
  "statistics": {
    "total_items": 88,
    "entities": 76,
    "relations": 68,
    "entities_merged": 1,
    "relations_merged": 0
  }
}
```

### Event 合并结果

```json
{
  "extraction_type": "event",
  "source_file": "path/to/file_event_kg.json",
  "chunks": 4,
  "data": [
    {
      "index": 0,
      "entity_relation_dict": {...},
      "event_entity_relation_dict": {...},
      ...
    },
    ...
  ]
}
```

---

## 消歧算法

### 1. 相似度计算

使用 Python 的 `SequenceMatcher` 计算字符串相似度：

```python
def _string_similarity(s1: str, s2: str) -> float:
    return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()
```

**阈值**: 0.85（可在函数调用时调整）

### 2. 候选分组

```python
groups = _find_similar_candidates(items, threshold=0.85)
# 结果: [["Mara", "Mara Ellison"], ["lighthouse", "eastern lighthouse"]]
```

### 3. LLM 消歧

将每组候选发送给 LLM，获取映射关系：

**Prompt 示例**：
```
You are a knowledge graph expert. Given these similar entity names, 
determine which ones refer to the same thing and provide a canonical name.

Entity names:
["Mara", "Mara Ellison"]

Return a JSON object mapping each original name to its canonical name.
```

**LLM 响应**：
```json
{
  "Mara": "Mara Ellison",
  "Mara Ellison": "Mara Ellison"
}
```

---

## 性能数据

基于 `the_lighthouse_last_signal.pdf`（4 chunks）的测试：

| 提取类型 | 原始项数 | 合并后项数 | 去重数 | 实体消歧 | 关系消歧 | 耗时 |
|---------|---------|-----------|--------|---------|---------|------|
| Triples | 92 | 88 | 4 | 1 | 0 | ~14s |
| Temporal | 56 | 56 | 0 | 0 | 0 | ~10s |
| Hyper | 74 | 74 | 0 | 1 | 0 | ~12s |

**注意**：消歧时间主要取决于相似候选组的数量和 LLM 响应速度。

---

## 代码位置

```
src/kgagent/extraction/
├── merge.py                # 合并功能主模块 ✨ 新增
├── kg_entry.py             # 提取入口（已修改：统一字段名）
├── document_processor.py   # 文档预处理
└── __init__.py             # 导出 merge_kg_chunks

src/kgagent/api/
└── chat.py                 # Chat 模式（已修改：集成自动合并）
```

---

## 配置选项

合并函数支持以下参数：

```python
async def merge_kg_chunks(
    kg_file: str | Path,              # 输入：chunk 级别的 KG 文件
    extraction_type: str,              # 提取类型
    llm_client: Any,                   # LLM 客户端
    output_file: str | Path | None = None,  # 输出文件（可选）
    similarity_threshold: float = 0.85,     # 相似度阈值（内部使用）
) -> dict[str, Any]
```

---

## 测试

运行测试脚本：

```bash
# 测试合并功能
python /tmp/test_kg_merge.py

# 测试端到端流程
python /tmp/test_end_to_end_merge.py
```

---

## 已知限制

1. **Event 类型不消歧**：由于结构复杂，Event 类型只进行简单合并
2. **LLM 依赖**：消歧功能依赖 LLM，如果 LLM 响应失败，会回退到使用第一个候选作为标准名
3. **相似度阈值固定**：目前阈值硬编码为 0.85，未来可以考虑作为参数暴露

---

## 未来改进

1. **可配置阈值**：允许用户调整相似度阈值
2. **批量 LLM 调用**：将多个候选组合并为一次 LLM 调用，提高效率
3. **Event 类型消歧**：为 Event 类型设计专门的消歧逻辑
4. **增量合并**：支持增量添加新 chunks 而不是重新合并全部
5. **统计报告**：生成详细的消歧报告，包括所有重命名操作

---

**文档生成时间**: 2026-07-31  
**版本**: v1.0 - 初始版本
