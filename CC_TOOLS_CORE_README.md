# CC Tools Core Release

这是CC Tools的核心组件发布分支，包含了完整的Claude-Code风格工具集成。

## 📦 包含的组件

### 🏗️ 核心架构模块 (`workers/core/`)

1. **`cc_tool_base.py`** - CC Tools统一基类
   - 简化继承：`AgenticBaseTool` → `CCToolBase`
   - Claude-Code风格接口：`get_detailed_prompt()`, `is_read_only()`
   - 自动prompt.py加载功能
   - 完整VERL兼容性

2. **`system_prompts.py`** - 系统提示词管理
   - 参考Claude-Code的纯净提示词设计
   - 环境信息收集和注入
   - 简洁风格："Keep responses short and focused"

3. **`enhanced_tool_manager.py`** - 工具管理器
   - 工具注册和权限控制
   - 动态schema生成
   - 只读/危险工具分类

4. **`context_manager.py`** - 上下文管理
   - Git状态自动收集
   - 项目结构分析
   - README和文档检测

5. **`config_manager.py`** - 配置管理
   - 项目和全局配置
   - 权限策略管理
   - 用户偏好设置

6. **`query_orchestrator.py`** - 查询编排
   - LLM对话流程管理
   - 工具调用循环
   - 错误处理和超时控制

### 🛠️ CC Tools工具套件 (`workers/tools/cc_tools/`)

包含16个完整的Kubernetes集成工具：

#### 核心工具
- **`file_read_tool/`** - 文件读取（只读）
- **`file_write_tool/`** - 文件写入
- **`file_edit_tool/`** - 文件编辑
- **`bash_tool/`** - Bash命令执行
- **`ls_tool/`** - 目录列表

#### 高级工具
- **`glob_tool/`** - 文件模式匹配
- **`grep_tool/`** - 内容搜索
- **`nb_read_tool/`** - Jupyter笔记本读取
- **`nb_edit_tool/`** - Jupyter笔记本编辑

#### 专业工具
- **`agent_tool/`** - 智能体工具
- **`architect_tool/`** - 架构分析工具
- **`think_tool/`** - 思考工具
- **`memory_read_tool/`** - 内存读取
- **`memory_write_tool/`** - 内存写入
- **`mcp_tool/`** - MCP协议工具
- **`sticker_request_tool/`** - 贴纸请求工具

每个工具都包含：
- `k8s_*_tool.py` - 主要实现
- `prompt.py` - Claude-Code风格的详细描述
- `__init__.py` - 模块初始化

### 🧪 测试框架

1. **主测试框架**:
   - `tests/test_cc_tools_general_agent_on_swe_subprocess.py`
   - 完整的SWE Bench风格测试
   - 支持多种LLM后端
   - 轨迹记录和分析

2. **单元测试套件** (`tests/cc_tooling/`):
   - 14个工具的独立测试
   - 覆盖核心功能和边界情况
   - K8s环境模拟

## 🎯 关键特性

### Claude-Code风格集成
- **纯净系统提示词**: 不包含工具描述，专注对话规则
- **动态工具描述**: 从prompt.py文件动态加载
- **权限管理**: 自动识别只读/危险工具
- **简洁响应**: "少即是多"的设计哲学

### VERL完全兼容
- **双重接口**: 支持`Tool(config)`和`Tool(config, tool_schema)`
- **实例生命周期**: `create_instance` → `execute_tool` → `release_instance`
- **异步支持**: 完整的异步执行框架

### K8s原生支持
- **Pod内执行**: 所有工具在Kubernetes Pod中运行
- **安全控制**: 路径验证、命令过滤、权限检查
- **资源管理**: 自动清理和超时处理

## 🚀 快速开始

### 基本使用

```python
from workers.tools.cc_tools.file_read_tool.k8s_file_read_tool import K8sFileReadTool
from workers.core.cc_tool_base import CCToolBase

# 创建工具实例
config = {
    "pod_name": "my-pod",
    "namespace": "default"
}

# 方式1: 直接使用
tool = K8sFileReadTool(config)

# 方式2: VERL兼容
tool = K8sFileReadTool(config, tool_schema=None)

# 获取Claude-Code风格描述
description = tool.get_detailed_prompt()
print(f"工具描述: {description}")

# 检查工具属性
print(f"只读工具: {tool.is_read_only()}")
print(f"工具启用: {tool.is_enabled()}")
```

### 与现有系统集成

```python
# 替换原有工具
from workers.tools.cc_tools.bash_tool.k8s_bash_tool import K8sBashTool

# CC Tools可以直接替换R2E工具
cc_bash = K8sBashTool(config)
# 使用相同的VERL接口
instance_id = await cc_bash.create_instance()
result = await cc_bash.execute_tool(instance_id, {"command": "ls -la"})
```

## 📊 性能特点

- **简洁继承**: 2层继承 vs 原来的4层
- **高效加载**: 自动缓存工具描述
- **并发支持**: 完整异步架构
- **内存优化**: 按需加载组件

## 🔧 依赖要求

- Python 3.8+
- kodo (Kubernetes管理)
- 现有的AgenticBaseTool框架

## 📝 使用说明

这个核心发布包含了CC Tools的所有必要组件，可以：

1. **独立使用**: 作为完整的工具套件
2. **集成现有系统**: 替换或补充现有工具
3. **扩展开发**: 基于CCToolBase开发新工具

## 🔗 相关链接

- [主仓库](https://github.com/ts2m/agentic-rollout-library)
- [Claude-Code原始项目](https://github.com/anthropics/claude-code)
- [kodo Kubernetes管理库](https://github.com/baidubce/kodo)

## ⚡ 版本信息

- **分支**: cc-tools-core-release
- **核心组件**: 6个模块
- **工具数量**: 16个K8s工具
- **测试覆盖**: 完整单元测试 + 集成测试
- **兼容性**: VERL + R2E + Claude-Code风格

---

**CC Tools: Claude-Code在Kubernetes环境的完美实现！** 🚀
