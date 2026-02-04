# Agentic Rollout Library - Claude Code 指南

## 项目概述

这是一个用于构建 Agentic 系统的 Python 库，提供工具执行、LLM 集成和 Kubernetes 支持。

## 环境配置

### Python 环境
使用 venv 执行 python3 的程序：

```bash
# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
pip install -e .
```

### 运行测试

```bash
# 在venv中运行测试
source venv/bin/activate
python -m pytest src/tests/
```

## 项目结构

```
agentic-rollout-library/
├── src/
│   ├── core/           # 核心节点实现
│   │   ├── base_node.py
│   │   ├── llm_node.py
│   │   ├── tool_execution_node.py
│   │   ├── k8s_tool_execution_node.py
│   │   ├── context_engineering_node.py
│   │   └── tool_parsing_node.py
│   ├── tools/          # 工具实现
│   │   ├── base_tool.py
│   │   ├── r2e/        # R2E工具集
│   │   ├── miaoda/     # Miaoda工具集
│   │   └── deepseek/   # DeepSeek工具集
│   └── utils/          # 工具函数
├── examples/           # 使用示例
├── docs/              # 文档
└── .claude/           # Claude Code skills
    └── skills/        # 技能文档
```

## 代码风格

- 使用 Python 3.7+ 语法
- 遵循 PEP 8 代码规范
- 使用类型注解 (Type Hints)
- 函数和类需要添加 docstring

## 关键文件

- `src/tools/base_tool.py` - Tool 基类，所有工具的封装器
- `src/core/tool_execution_node.py` - 工具执行节点
- `src/core/llm_node.py` - LLM 集成节点

## 开发注意事项

1. **工具开发**: 参考 `.claude/skills/writing-tools.md` 了解如何编写新工具
2. **异步支持**: 所有节点支持异步执行 (`process_async()`)
3. **K8S执行**: 工具可以在本地或 K8S Pod 中执行
4. **安全性**: 注意检查命令注入等安全问题

## 常用命令

```bash
# 运行单个工具测试
python -m src.tools.tests.r2e.test_bash_func

# 运行所有测试
python -m pytest src/tests/ -v

# 代码格式化
black src/

# 类型检查
mypy src/
```
