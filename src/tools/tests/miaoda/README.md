

# Miaoda Tools Test Suite

完整的 Miaoda 工具测试套件，包括本地函数执行测试和**真实 K8S Pod 执行测试**。

> **重要更新**：测试套件已重构为真正在 K8S 上执行测试，参考 `miaoda_k8s_example.py` 的实现方式，确保测试环境与生产环境一致。

## 目录结构

```
src/tools/tests/miaoda/
├── __init__.py                 # Package init
├── test_base.py                # 测试基类和工具函数
├── test_think.py               # think 工具测试
├── test_finish.py              # finish 工具测试
├── test_supabase.py            # Supabase 工具测试（init, migration, sql_execution）
├── run_all_tests.py            # 主测试运行器
└── README.md                   # 本文件
```

## 测试的工具

### 已测试工具

1. **think.py** - 思考/推理记录工具
   - ✅ 本地函数测试
   - ✅ **真实 K8S Pod 执行测试**
   - ✅ 特殊字符测试
   - ✅ 错误处理测试

2. **finish.py** - 任务完成/提交工具
   - ✅ 本地函数测试
   - ✅ **真实 K8S Pod 执行测试**
   - ✅ 参数验证测试
   - ✅ 特殊字符测试
   - ✅ 错误处理测试

3. **supabase_init.py** - Supabase 初始化工具
   - ✅ 本地函数测试
   - ✅ **真实 K8S Pod 执行测试**
   - ✅ 特殊字符测试

4. **supabase_migration.py** - Supabase 数据库迁移工具
   - ✅ 本地函数测试
   - ✅ **真实 K8S Pod 执行测试**
   - ✅ 复杂 SQL 测试
   - ✅ 问题字符测试（引号、反斜杠、&、;、$等）

5. **supabase_sql_execution.py** - Supabase SQL 执行工具
   - ✅ 本地函数测试
   - ✅ **真实 K8S Pod 执行测试**
   - ✅ 复杂查询测试

## 使用方法

### 运行所有测试

```bash
# 从项目根目录
cd src
python tools/tests/miaoda/run_all_tests.py
```

### 运行特定工具的测试

```bash
# 只测试 think 工具
python tools/tests/miaoda/run_all_tests.py --tool think

# 只测试 finish 工具
python tools/tests/miaoda/run_all_tests.py --tool finish

# 只测试 supabase 工具
python tools/tests/miaoda/run_all_tests.py --tool supabase
```

### 运行单个测试文件

```bash
# 测试 think 工具
python tools/tests/miaoda/test_think.py

# 测试 finish 工具
python tools/tests/miaoda/test_finish.py

# 测试 supabase 工具
python tools/tests/miaoda/test_supabase.py
```

### 详细输出模式

```bash
python tools/tests/miaoda/run_all_tests.py --verbose
```

## K8S 测试架构

### test_base.py - 核心测试基类

提供了完整的 K8S Pod 测试支持：

1. **create_k8s_executor(test_name)** - 创建 K8S 执行器
   - 自动生成唯一的 pod 名称
   - 配置 namespace、kubeconfig、image 等参数
   - 与 `miaoda_k8s_example.py` 使用相同配置

2. **initialize_miaoda_pod(executor, app_id)** - 初始化 Pod 环境
   - 设置 node_modules 符号链接
   - 复制代码模板到 workspace
   - 安装必要的 Python 包

3. **register_miaoda_tools(executor)** - 注册所有 Miaoda 工具
   - 注册 bash_executor, file_editor, think, finish 等
   - 注册 Supabase 相关工具

4. **execute_tool_in_k8s(tool_name, parameters, test_name)** - 在真实 K8S Pod 中执行工具
   - 创建并初始化 pod
   - 执行工具调用
   - 返回执行结果
   - 自动清理 pod（使用 async context manager）

5. **assert_success(result, test_name)** / **assert_failure(result, test_name)** - 验证测试结果

### K8S 配置（与生产环境一致）

- **Namespace**: `rl-training`
- **Kubeconfig**: `./swe-bench-verified-workspace/config_cce_new`
- **Image**: `iregistry.baidu-int.com/acg-agi/reward:miaoda_reward_1106`
- **Resources**: CPU 0.3 core, Memory 1Gi
- **Tool Timeout**: 60s
- **Environment**: 包含代理、Python 路径等完整环境变量

## 测试覆盖

### 测试类型

每个工具都进行以下类型的测试：

1. **本地函数执行测试**
   - 测试工具函数是否正确执行
   - 验证返回值格式和内容
   - 测试错误处理

2. **真实 K8S Pod 执行测试**（新增）
   - 在真实的 K8S Pod 中执行工具
   - 验证完整的执行流程（初始化 → 注册工具 → 执行 → 清理）
   - 测试与生产环境的一致性

3. **特殊字符测试**
   - 单引号 `'`
   - 双引号 `"`
   - 反斜杠 `\`
   - Shell 特殊字符：`&`, `;`, `|`, `$`, `` ` ``
   - 换行符、制表符
   - SQL 注释：`--`, `/* */`
   - Unicode 字符

4. **边界条件测试**
   - 空输入
   - 空字符串
   - 多行文本
   - 非常长的输入

### 测试示例

#### Think 工具测试示例

```python
# 本地函数测试
def test_local_simple_thought(self):
    result = think_func("I need to analyze the codebase")
    assert result["status"] == "success"
    print("✅ test_local_simple_thought: PASSED")

# K8S Pod 执行测试（新）
async def test_k8s_simple_thought(self):
    result = await self.execute_tool_in_k8s(
        tool_name="miaoda_think",
        parameters={"thought": "Analyzing the problem"},
        test_name="simple-thought"
    )
    self.assert_success(result, "test_k8s_simple_thought")
```

#### Supabase Migration 测试示例

```python
# 本地函数测试
def test_local_complex_migration(self):
    sql = """-- Create users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE
);"""
    result = supabase_migration_func("create_users", sql)
    assert result["status"] == "success"

# K8S Pod 执行测试（新）
async def test_k8s_complex_migration(self):
    sql = """-- Create profiles table
CREATE TABLE IF NOT EXISTS profiles (
    id UUID PRIMARY KEY,
    phone VARCHAR(20) UNIQUE NOT NULL,
    full_name VARCHAR(100) NOT NULL
);"""
    result = await self.execute_tool_in_k8s(
        tool_name="miaoda_supabase_migration",
        parameters={"name": "create_profiles", "query": sql},
        test_name="supabase-migration-complex"
    )
    self.assert_success(result, "test_k8s_complex_migration")
```

## 测试结果输出

测试成功时的输出：

```
================================================================================
THINK TOOL TEST SUITE
================================================================================

[Local Function Tests]
✅ test_local_simple_thought: PASSED
✅ test_local_empty_thought: PASSED
✅ test_local_complex_thought: PASSED
✅ test_local_special_characters: PASSED

[K8S Pod Execution Tests]
✅ test_k8s_simple_thought: PASSED
   Result: I am thinking...: Analyzing the problem
✅ test_k8s_thought_with_quotes: PASSED
   Result: I am thinking...: It's a 'quoted' thought with "double quotes"
✅ test_k8s_unicode_thought: PASSED
   Result: I am thinking...: 思考：我需要分析这个问题 🤔

================================================================================
✅ ALL THINK TOOL TESTS PASSED
================================================================================
```

## 添加新测试

### 为新工具添加测试

1. **创建测试文件**

   在 `src/tools/tests/miaoda/` 目录下创建 `test_<tool_name>.py`

2. **继承测试基类**

   ```python
   from tools.tests.miaoda.test_base import MiaodaToolTestBase

   class TestMyTool(MiaodaToolTestBase):
       """Test cases for my_tool."""
   ```

3. **编写测试方法**

   ```python
   # 本地函数测试
   def test_local_basic(self):
       """Test basic functionality."""
       result = my_tool_func("input")
       assert result["status"] == "success"
       print("✅ test_local_basic: PASSED")

   # K8S Pod 执行测试（新）
   async def test_k8s_execution(self):
       """Test execution in real K8S pod."""
       result = await self.execute_tool_in_k8s(
           tool_name="miaoda_my_tool",
           parameters={"param": "value"},
           test_name="my-tool-test"
       )
       self.assert_success(result, "test_k8s_execution")
   ```

4. **添加到主测试运行器**

   在 `run_all_tests.py` 中添加新工具的测试：

   ```python
   from tools.tests.miaoda import test_my_tool

   def run_my_tool_tests():
       """Run my_tool tests."""
       print("\n" + "╔" + "═" * 78 + "╗")
       print("║" + " " * 28 + "MY TOOL TESTS" + " " * 36 + "║")
       print("╚" + "═" * 78 + "╝")
       test_my_tool.run_all_tests()
   ```

4. **添加异步支持**

   在 `run_all_tests` 函数中使用异步包装器：

   ```python
   async def run_all_tests_async():
       """Run all tests (async version)."""
       test = TestMyTool()

       # 本地测试（同步）
       print("\n[Local Function Tests]")
       test.test_local_basic()

       # K8S 测试（异步）
       print("\n[K8S Pod Execution Tests]")
       await test.test_k8s_execution()

   def run_all_tests():
       """Run all tests (sync wrapper)."""
       loop = asyncio.new_event_loop()
       asyncio.set_event_loop(loop)
       try:
           loop.run_until_complete(run_all_tests_async())
       finally:
           loop.close()
   ```

## K8S 测试执行流程

每个 K8S 测试的完整执行流程：

1. **创建 K8S Pod**
   - 使用唯一的 pod 名称（包含测试名称和随机 UUID）
   - 配置资源限制（CPU 0.3 core, Memory 1Gi）
   - 设置环境变量（代理、Python 路径等）

2. **初始化 Pod 环境**
   - 删除旧的 node_modules 符号链接
   - 创建新的 node_modules 符号链接到 `/data/shadcn/node_modules`
   - 复制 React 模板到工作目录
   - 安装必要的 Python 包（如 chardet）

3. **注册工具**
   - 注册所有 Miaoda 工具到 K8S 执行器
   - 包括 bash_executor, file_editor, think, finish
   - 包括 Supabase 相关工具

4. **执行工具**
   - 使用 `executor.process_async()` 执行工具调用
   - 传递工具名称和参数
   - 获取执行结果

5. **验证结果**
   - 检查 status 字段（success/error/stop）
   - 验证返回的数据格式和内容
   - 断言测试通过或失败

6. **清理资源**
   - 通过 async context manager 自动删除 pod
   - 释放 K8S 资源

## 常见问题

### Q: 测试失败了怎么办？

A: 检查以下几点：
1. 确保在 `src/` 目录下运行测试
2. 检查 Python 路径是否正确
3. 查看错误信息中的具体失败原因
4. 使用 `--verbose` 模式获取更多信息

### Q: 如何添加新的测试用例？

A: 在相应的测试文件中添加新的测试方法，遵循命名约定 `test_local_*` 或 `test_k8s_*`

### Q: 为什么需要同时测试本地和 K8S？

A:
- **本地测试**验证工具函数的核心逻辑，快速发现基本问题
- **K8S Pod 执行测试**验证在真实生产环境中的执行，确保与 `miaoda_k8s_example.py` 行为一致
- 两者结合确保完整的测试覆盖

### Q: K8S 测试需要多久？

A:
- 本地测试通常在几秒内完成
- K8S 测试需要创建和删除 pod，每个测试约需 30-60 秒
- 建议在本地快速迭代，在提交前运行完整的 K8S 测试

### Q: K8S 测试失败如何调试？

A:
1. 检查 pod 是否成功创建：`kubectl get pods -n rl-training`
2. 查看 pod 日志：`kubectl logs <pod-name> -n rl-training`
3. 检查资源配额是否足够
4. 验证 kubeconfig 路径是否正确
5. 确认可以拉取 Docker 镜像

## 贡献指南

添加新测试时请确保：

1. ✅ 测试命名清晰描述测试目的
2. ✅ 包含正向和负向测试用例
3. ✅ 测试特殊字符和边界条件
4. ✅ 添加适当的断言和错误信息
5. ✅ 更新本 README 文档

## 相关文档

- `FIX_SUMMARY.md` - Supabase 工具修复总结
- `SUPABASE_TOOLS_DEBUG_REPORT.md` - 详细问题分析报告
- 各工具源代码：`src/tools/miaoda/*.py`
