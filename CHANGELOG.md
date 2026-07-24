# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0] - 2024-07-24

### 重大重构 - DataFlow 风格架构

完全重构项目，采用 DataFlow-Table_SDK 风格的清晰分层架构。

#### Added

- **统一入口点** - `KGAgentSystem` 类提供 Python API 和 CLI 统一接口
- **模块化架构** - 清晰的 `core/`, `system/`, `extraction/` 分层
- **配置管理** - 支持环境变量 + `config.py` + `config.local.py` 配置方式
- **批处理支持** - 通过 `extract_batch()` 实现并发批量提取
- **命令行增强** - 新增 `extract` 和 `batch` 子命令
- **改进的 JSON 解析** - 支持从多种格式中提取 JSON 结果
- **验证工具简化** - 将 record-agent 功能简化为独立的验证函数

#### Changed

- **目录结构**：
  ```
  旧: src/kgagent/
      ├── agents/
      ├── modules/
      ├── tools/
      └── prompts/
  
  新: src/kgagent/
      ├── core/              # 共享基础设施
      ├── system/            # 系统层（orchestrator, registry）
      ├── extraction/        # 提取路由
      │   ├── agents/
      │   └── tools/
      └── api/               # CLI 和 chat
  ```

- **API 重构**：
  - 旧: `from kgagent.modules import build_supervisor_options`
  - 新: `from kgagent import KGAgentSystem`

- **配置方式**：
  - 旧: 硬编码模型名称
  - 新: 环境变量 `KG_MODEL`, `KG_API_URL`, `KG_API_KEY`

#### Removed

- `modules/` 目录 - 功能分散到 `system/` 和 `extraction/`
- `record-agent` - 简化为验证函数
- Supervisor 过度复杂的路由逻辑

#### Fixed

- JSON 解析失败问题 - 改进正则表达式提取
- 模型名称配置错误 - 移除硬编码模型名
- Import 循环依赖问题
- ExtractionRegistry 关键词匹配优先级问题

### 架构改进

1. **清晰的职责分离**
   - Core: 配置、日志、I/O
   - System: 编排、路由、注册
   - Extraction: 实际提取逻辑
   - API: 用户接口

2. **更好的可扩展性**
   - ExtractionRegistry 支持动态注册新类型
   - 模块化设计便于添加新功能

3. **更强的类型安全**
   - 完整的类型注解
   - 清晰的配置类

## [0.2.0] - 2024-07-24

### Changed

- 简化 supervisor prompt，移除过度叙述
- 修复 extraction agent 模型配置

## [0.1.0] - Initial Release

- 基础知识图谱提取功能
- 支持三种提取类型：triples, temporal, hyper
- 交互式 chat 界面
