# Verify Unitest - SWE-bench 验证测试套件

这是一个用于 SWE-bench 数据集验证的完整测试套件，提供从 patch 处理到最终报告生成的完整工作流程。

## 📋 工作流程概述

```
原始 Patches → 清理 Patches → 插入到 JSONL → 生成评估脚本 → 运行测试 → 生成报告
```

## 🛠️ 工具说明

### 1. Patch 处理工具 (`patch/`)

#### `remove_pyproject_diff.py`
**功能**: 移除 patch 文件中关于 `pyproject.toml` 的 diff 内容

**用途**: 清理 patch 文件，只保留代码相关的修改，移除构建配置相关的变更

**使用方法**:
```bash

# 修改脚本中的路径配置，然后运行
python patch/remove_pyproject_diff.py

# 或直接编辑脚本中的路径：
# input_folder = ".../patches"  # 修改为你的输入文件夹路径
# output_folder = "../patches_clean"  # 修改为你的输出文件夹路径
```

**处理逻辑**:
- 解析 patch 文件的 diff 块
- 识别并跳过 `pyproject.toml` 文件的修改
- 保留其他所有文件的修改
- 重新构建干净的 patch 文件

#### `insert_patches_to_jsonl.py`
**功能**: 将清理过的 patch 文件内容插入到 JSONL 数据集的 `ours_patch` 字段中

**用途**: 为 SWE-bench 数据集的每个实例添加对应的 patch 内容

**使用方法**:
```bash
# 修改脚本中的路径配置，然后运行
python patch/insert_patches_to_jsonl.py

# 或直接编辑脚本中的路径：
# jsonl_path = "../test-00000-of-00001-with-images.jsonl"  # 修改为你的 JSONL 文件路径
# patches_dir = "../patch_clean_mini"  # 修改为你的清理过的 patches 文件夹路径
```

**配置**:
- `jsonl_path`: 输入的 JSONL 文件路径 (默认: `../test-00000-of-00001-with-images.jsonl`)
- `patches_dir`: 清理过的 patches 文件夹路径 (默认: `../patch_clean_mini`)

**输出**:
- 生成带有 `ours_patch` 字段的 JSONL 文件
- 对于没有找到对应 patch 的记录，`ours_patch` 字段设为 `null`

### 2. 评估脚本生成工具 (`generate_eval_script.py`)

**功能**: 为每个 SWE-bench 实例生成评估脚本 (eval.sh)

**用途**: 创建与 SWE-bench 格式一致的评估脚本，用于运行单元测试

**主要特性**:
- 从 JSONL 文件读取实例信息
- 生成标准化的评估脚本
- 支持多种测试框架 (pytest, unittest 等)
- 与 SWE-bench 保持格式一致性

**使用方法**:
```bash
# 从 JSONL 文件批量生成评估脚本
python generate_eval_script.py --jsonl-file dataset_with_patches.jsonl --output-dir eval_scripts/

# 生成单个实例的评估脚本
python generate_eval_script.py --instance-id astropy__astropy-12345 --base-commit abc123 --test-patch patch_content.txt --output-dir eval_scripts/

# 限制生成的脚本数量
python generate_eval_script.py --jsonl-file dataset.jsonl --limit 10 --output-dir eval_scripts/
```

**主要参数**:
- `--jsonl-file`: JSONL 文件路径（批量生成时使用）
- `--instance-id`: 实例 ID（单个生成时使用）
- `--base-commit`: 基准提交哈希
- `--test-patch`: 测试补丁内容
- `--output-dir`: 输出目录
- `--limit`: 限制生成的脚本数量

**生成内容**:
- 测试环境设置
- 代码应用 patch
- 运行单元测试
- 结果收集和日志保存

### 3. 日志生成工具 (`generate_logs.py`)

**功能**: 运行生成的评估脚本，收集测试结果日志

**用途**: 执行所有评估脚本，保存测试过程中的详细日志

**使用方法**:
```bash
# 基本使用
python generate_logs.py dataset.jsonl --output-dir ./patches/

# 指定并发数和超时时间
python generate_logs.py dataset.jsonl --output-dir ./patches/ --max-concurrent 4 --timeout 1200

# 本地调试模式
python generate_logs.py dataset.jsonl --local-mode --output-dir ./patches/

# 使用 K8s
python generate_logs.py dataset.jsonl --namespace default --kubeconfig ~/.kube/config --output-dir ./patches/
```

**主要参数**:
- `jsonl_file`: JSONL 数据集文件路径
- `--output-dir`: 输出目录（默认: `./swe_patches`）
- `--max-concurrent`: 最大并发数（默认: 1）
- `--timeout`: 超时时间（秒，默认: 600）
- `--local-mode`: 本地调试模式
- `--namespace`: K8s 命名空间
- `--kubeconfig`: K8s 配置文件路径

**日志类型**:
- **p2p (pass-to-pass)**: 验证原有测试仍然通过
- **f2p (fail-to-pass)**: 验证修复后的测试能够通过
- 详细的测试输出和错误信息

### 4. 报告生成工具 (`generate_report.py`)

**功能**: 分析测试日志，生成详细的统计报告

**用途**: 统计单元测试的通过情况，生成最终的评估报告

**使用方法**:
```bash
# 基本使用
python generate_report.py --logs-dir ./test_logs/ --output report.json

# 指定 gold results 进行对比
python generate_report.py --logs-dir ./test_logs/ --gold-results dataset.jsonl --output report.json

# 排除特定实例
python generate_report.py --logs-dir ./test_logs/ --exclude-file exclude_instances.json --output report.json

# 完整参数
python generate_report.py --logs-dir /path/to/logs --gold-results dataset.jsonl --exclude-file exclude.json --output final_report.json
```

**主要参数**:
- `--logs-dir`: 日志文件目录路径（默认: 系统路径）
- `--gold-results`: gold results JSONL 文件路径（用于对比）
- `--output`: 输出报告文件路径（默认: 系统路径）
- `--exclude-file`: 要排除的实例 ID 列表文件

**报告内容**:
- 整体通过率统计
- f2p 和 p2p 测试的详细结果
- JSON 格式的结构化报告

## 🚀 使用流程

### 步骤 1: 准备数据
确保你有以下文件和文件夹：
- `patches/` - 包含原始 patch 文件的文件夹
- `dataset.jsonl` - SWE-bench 格式的 JSONL 数据集文件

### 步骤 2: 清理 Patches
```bash
# 清理 pyproject.toml 相关的修改
python patch/remove_pyproject_diff.py --input-folder patches/ --output-folder patches_clean/
```

### 步骤 3: 插入 Patches 到 JSONL
```bash
# 将清理过的 patches 插入到 JSONL 文件中
python patch/insert_patches_to_jsonl.py
```

这会在每个 JSONL 记录中添加 `ours_patch` 字段。

### 步骤 4: 生成评估脚本
```bash
# 为每个实例生成 eval.sh 脚本
python generate_eval_script.py --jsonl dataset_with_patches.jsonl --output-dir eval_scripts/
```

### 步骤 5: 运行测试并收集日志
```bash
# 运行测试并收集日志
python generate_logs.py dataset_with_patches.jsonl --output-dir test_logs/

# 或者指定并发数提高效率
python generate_logs.py dataset_with_patches.jsonl --output-dir test_logs/ --max-concurrent 4
```

### 步骤 6: 生成最终报告
```bash
# 分析日志并生成统计报告
python generate_report.py --logs-dir test_logs/validations/ --output report.json
```

## ⚙️ 配置说明

### 文件路径配置

对于没有命令行参数的脚本，需要直接修改脚本中的路径配置：

#### `remove_pyproject_diff.py` 配置:
```python
# 修改脚本开头的路径配置
input_folder = ".../patches"  # 修改为你的输入文件夹路径
output_folder = "../patches_clean"  # 修改为你的输出文件夹路径
```

#### `insert_patches_to_jsonl.py` 配置:
```python
# 修改脚本中的路径配置
jsonl_path = "../dataset.jsonl"  # 修改为你的 JSONL 文件路径
patches_dir = "../patches_clean"  # 修改为你的清理过的 patches 文件夹路径
```

#### 有命令行参数的脚本配置:
```bash
# generate_eval_script.py
python generate_eval_script.py --jsonl-file dataset.jsonl --output-dir eval_scripts/

# generate_logs.py
python generate_logs.py dataset.jsonl --output-dir logs/ --max-concurrent 4

# generate_report.py
python generate_report.py --logs-dir logs/ --gold-results dataset.jsonl --output report.json
```

### 环境变量
某些脚本可能需要设置环境变量：

```bash
# K8s 相关
export K8S_NAMESPACE="default"
export KUBECONFIG="~/.kube/config"
```


