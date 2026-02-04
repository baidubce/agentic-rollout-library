# 编写 Tools 指南

本文档介绍如何在 Agentic Rollout Library 中编写新的工具 (Tools)。

## 工具架构概述

工具系统由以下部分组成：

1. **Tool 类** (`src/tools/base_tool.py`) - 工具封装器
2. **工具脚本** - 实际执行逻辑的 Python 脚本
3. **ToolExecutionNode** - 执行工具的节点

## 工具文件结构

每个工具是一个独立的 Python 脚本，包含以下标准结构：

```python
#!/usr/bin/env python3
"""
Description: 工具描述（简短说明工具的功能）
Parameters:
  param1 (type, required/optional): 参数描述
  param2 (type, required/optional): 参数描述

Usage:
  As a script: python tool_name.py "arg1" "arg2"
  As a module: python -m tools.collection.tool_name "arg1"
  As a function: from tools.collection.tool_name import main_func; main_func("arg1")
"""

import argparse
import sys
from typing import Dict, Any


def main_func(param1: str, param2: str = None) -> Dict[str, Any]:
    """
    工具的核心函数。

    Args:
        param1: 参数1描述
        param2: 参数2描述 (可选)

    Returns:
        包含以下字段的字典:
            - output: 执行结果
            - status: 状态 ('success' 或 'error')
            - error: 错误信息 (如果有)
    """
    try:
        # 工具逻辑
        result = do_something(param1, param2)
        return {
            "output": result,
            "status": "success"
        }
    except Exception as e:
        return {
            "output": "",
            "error": str(e),
            "status": "error"
        }


def parse_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    解析工具执行结果供 Agent 使用。

    Args:
        result: 原始执行结果

    Returns:
        格式化后的结果
    """
    return result


def build_k8s_command(*args) -> str:
    """
    构建 K8S Pod 执行命令。

    Args:
        *args: 工具参数

    Returns:
        K8S 执行的命令字符串
    """
    import base64
    # 使用 base64 编码避免 shell 转义问题
    encoded_args = [base64.b64encode(str(arg).encode()).decode() for arg in args]
    return f'python3 -c "..."'


def main():
    """CLI 入口点。"""
    parser = argparse.ArgumentParser(description="工具描述")
    parser.add_argument("param1", help="参数1描述")
    parser.add_argument("--param2", default=None, help="参数2描述")
    parser.add_argument("--json", action="store_true", help="JSON格式输出")

    args = parser.parse_args()
    result = main_func(args.param1, args.param2)

    if args.json:
        import json
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result["status"] == "error":
            print(f"Error: {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(result["output"])


if __name__ == "__main__":
    main()
```

## 返回值规范

所有工具必须返回一个字典，包含以下字段：

| 字段 | 类型 | 必需 | 描述 |
|------|------|------|------|
| `output` | str | 是 | 执行结果/输出内容 |
| `status` | str | 是 | 状态: `success` 或 `error` |
| `error` | str | 否 | 错误信息 |
| `message` | str | 否 | 额外的描述信息 |

### 成功示例

```python
{
    "output": "文件已成功创建: /path/to/file.py",
    "status": "success",
    "message": "Created 1 file"
}
```

### 错误示例

```python
{
    "output": "",
    "error": "文件不存在: /path/to/file.py",
    "status": "error"
}
```

## 必需的三个函数

每个工具脚本必须实现以下三个函数：

### 1. 核心函数 (如 `bash_func`, `think_func`)

工具的主要逻辑实现：

```python
def tool_func(param1: str, **kwargs) -> Dict[str, Any]:
    """
    工具的核心执行逻辑。

    **kwargs 用于接收框架传递的额外参数:
    - app_id: 应用ID
    - user_id: 用户ID
    - session_id: 会话ID
    - trace_id: 追踪ID
    """
    pass
```

### 2. `parse_result` 函数

解析和格式化执行结果：

```python
def parse_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    解析工具结果，统一格式。

    可以处理本地执行和 K8S 执行的不同结果格式。
    """
    if isinstance(result, dict):
        return {
            "output": result.get("stdout", result.get("output", "")),
            "error": result.get("stderr", result.get("error", "")),
            "status": result.get("status", "success")
        }
    return {"output": str(result), "status": "success"}
```

### 3. `build_k8s_command` 函数

构建在 K8S Pod 中执行的命令：

```python
def build_k8s_command(param1: str) -> str:
    """
    构建 K8S 执行命令。

    重要: 使用 base64 编码处理特殊字符！
    """
    import base64
    encoded = base64.b64encode(param1.encode()).decode()
    return f'python3 -c "import base64, json; from tools.xxx import func; p = base64.b64decode(\'{encoded}\').decode(); print(json.dumps(func(p)))"'
```

## 安全注意事项

### 1. 输入验证

```python
# 检查危险命令
BLOCKED_COMMANDS = ["rm -rf /", "dd if=/dev/zero", ": (){:|:&};:"]

def validate_command(command: str) -> bool:
    for blocked in BLOCKED_COMMANDS:
        if blocked in command:
            return False
    return True
```

### 2. 路径安全

```python
import os

def safe_path(path: str, base_dir: str) -> str:
    """确保路径在允许的目录内。"""
    abs_path = os.path.abspath(os.path.join(base_dir, path))
    if not abs_path.startswith(os.path.abspath(base_dir)):
        raise ValueError("路径越界")
    return abs_path
```

### 3. 命令注入防护

```python
import shlex

def safe_command(user_input: str) -> str:
    """转义用户输入。"""
    return shlex.quote(user_input)
```

## 工具注册

将工具注册到 ToolExecutionNode：

```python
from src.core.tool_execution_node import ToolExecutionNode
from src.tools.base_tool import Tool

# 创建执行节点
executor = ToolExecutionNode(name="MyExecutor")

# 注册工具
executor.register_tool(
    "my_tool",
    "src/tools/my_collection/my_tool.py",
    execution_mode="local"  # 或 "k8s"
)

# 执行工具
result = executor.process([{
    "tool": "my_tool",
    "parameters": {"param1": "value1"}
}])
```

## 完整示例：文件搜索工具

```python
#!/usr/bin/env python3
"""
Description: Search for files matching a pattern in a directory.
Parameters:
  pattern (string, required): The glob pattern to search for
  directory (string, optional): Directory to search in (default: current)

Usage:
  As a script: python search_files.py "*.py" --directory /path/to/dir
"""

import argparse
import glob
import os
import sys
from typing import Dict, Any, List


def search_files(pattern: str, directory: str = ".") -> Dict[str, Any]:
    """
    Search for files matching a pattern.

    Args:
        pattern: Glob pattern (e.g., "*.py", "**/*.txt")
        directory: Base directory for search

    Returns:
        Dictionary with matching files and status
    """
    try:
        # 验证目录存在
        if not os.path.isdir(directory):
            return {
                "output": "",
                "error": f"Directory not found: {directory}",
                "status": "error",
                "files": []
            }

        # 执行搜索
        search_path = os.path.join(directory, pattern)
        matches = glob.glob(search_path, recursive=True)

        return {
            "output": "\n".join(matches) if matches else "No files found",
            "status": "success",
            "files": matches,
            "count": len(matches)
        }

    except Exception as e:
        return {
            "output": "",
            "error": str(e),
            "status": "error",
            "files": []
        }


def parse_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Parse search result for agent use."""
    return {
        "output": result.get("output", ""),
        "files": result.get("files", []),
        "count": result.get("count", 0),
        "status": result.get("status", "success")
    }


def build_k8s_command(pattern: str, directory: str = ".") -> str:
    """Build K8S execution command."""
    import base64
    encoded_pattern = base64.b64encode(pattern.encode()).decode()
    encoded_dir = base64.b64encode(directory.encode()).decode()

    return (
        f'python3 -c "'
        f'import base64, json; '
        f'from tools.search_files import search_files; '
        f'p = base64.b64decode(\"{encoded_pattern}\").decode(); '
        f'd = base64.b64decode(\"{encoded_dir}\").decode(); '
        f'print(json.dumps(search_files(p, d), ensure_ascii=False))'
        f'"'
    )


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Search for files matching a pattern",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "pattern",
        help="Glob pattern to search for (e.g., '*.py')"
    )
    parser.add_argument(
        "--directory", "-d",
        default=".",
        help="Directory to search in (default: current)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )

    args = parser.parse_args()
    result = search_files(args.pattern, args.directory)

    if args.json:
        import json
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result["status"] == "error":
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)

        print(f"Found {result['count']} files:")
        for f in result.get("files", []):
            print(f"  {f}")


if __name__ == "__main__":
    main()
```

## 测试工具

为每个工具创建测试文件：

```python
# src/tools/tests/my_collection/test_search_files.py
import unittest
import os
import tempfile
from tools.my_collection.search_files import search_files, parse_result


class TestSearchFiles(unittest.TestCase):

    def setUp(self):
        """Create temporary test directory."""
        self.test_dir = tempfile.mkdtemp()
        # Create test files
        open(os.path.join(self.test_dir, "test1.py"), 'w').close()
        open(os.path.join(self.test_dir, "test2.py"), 'w').close()
        open(os.path.join(self.test_dir, "test.txt"), 'w').close()

    def test_search_python_files(self):
        """Test searching for Python files."""
        result = search_files("*.py", self.test_dir)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 2)

    def test_search_nonexistent_directory(self):
        """Test searching in nonexistent directory."""
        result = search_files("*.py", "/nonexistent/path")
        self.assertEqual(result["status"], "error")

    def test_parse_result(self):
        """Test result parsing."""
        raw = {"output": "test", "files": ["a.py"], "count": 1, "status": "success"}
        parsed = parse_result(raw)
        self.assertEqual(parsed["count"], 1)


if __name__ == "__main__":
    unittest.main()
```

## 工具集组织

将相关工具组织到同一目录：

```
src/tools/
├── base_tool.py
├── __init__.py
├── my_collection/
│   ├── __init__.py
│   ├── search_files.py
│   ├── read_file.py
│   ├── write_file.py
│   └── arg_utils.py      # 共享工具函数
└── tests/
    └── my_collection/
        ├── __init__.py
        ├── test_search_files.py
        └── run_all_tests.py
```

## 最佳实践

1. **单一职责**: 每个工具只做一件事
2. **明确的文档字符串**: 描述参数、返回值和用法
3. **错误处理**: 捕获所有异常，返回有意义的错误信息
4. **类型注解**: 使用 Python 类型注解提高代码可读性
5. **可测试性**: 核心逻辑与 CLI 分离，便于单元测试
6. **幂等性**: 尽可能使工具操作可重复执行
7. **超时控制**: 对长时间运行的操作设置超时
8. **日志记录**: 在关键位置添加日志便于调试
