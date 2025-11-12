#!/usr/bin/env python3
"""
生成评估脚本的工具函数
从JSONL文件中读取参数并生成评估脚本
"""

import os
import json
from typing import Optional, List, Dict, Any
from unidiff import PatchSet


def get_modified_files(patch: str) -> list[str]:
    """
    Get the list of modified files in a patch
    """
    source_files = []

    # 尝试使用unidiff，如果失败则使用简单字符串解析
    try:
        for file in PatchSet(patch):
            if file.source_file != "/dev/null":
                source_files.append(file.source_file)
        source_files = [x[2:] for x in source_files if x.startswith("a/")]
    except Exception as e:
        print(f"Warning: unidiff parsing failed ({e}), using fallback method")
        # 回退方法：直接从diff行中提取文件路径
        lines = patch.split('\n')
        for line in lines:
            if line.startswith('diff --git'):
                parts = line.split()
                if len(parts) >= 3:
                    file_path = parts[2]
                    if file_path.startswith('a/'):
                        source_files.append(file_path[2:])

    return list(set(source_files))  # 去重


def read_jsonl_file(jsonl_path: str) -> List[Dict[str, Any]]:
    """
    从JSONL文件中读取所有记录

    Args:
        jsonl_path: JSONL文件路径

    Returns:
        记录列表
    """
    records = []
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def extract_test_info_from_patch(test_patch: str) -> tuple[list[str], str]:
    """
    从补丁内容中提取测试文件列表和测试命令

    Args:
        test_patch: 补丁内容

    Returns:
        (test_files, run_command) 元组
    """
    # 获取所有修改的文件
    test_files = get_modified_files(test_patch)

    # 过滤出测试文件
    test_files = [f for f in test_files if 'test' in f.lower() and f.endswith('.py')]

    if not test_files:
        # 如果没有找到测试文件，默认使用一个
        test_files = ["test_file.py"]

    # 生成测试运行命令 - 运行所有测试文件
    test_files_str = ' '.join(test_files)
    run_command = f"PYTHONWARNINGS='ignore::UserWarning,ignore::SyntaxWarning' python -m pytest {test_files_str} -v"

    return test_files, run_command


def generate_eval_script_from_record(
    record: Dict[str, Any],
    output_dir: Optional[str] = None
) -> str:
    """
    从JSONL记录生成评估脚本

    Args:
        record: JSONL记录
        output_dir: 输出目录

    Returns:
        生成的脚本文件路径
    """
    base_commit = record.get('base_commit', '')
    instance_id = record.get('instance_id', 'unknown')
    test_patch = record.get('test_patch', '')

    # 从补丁中提取测试文件列表和命令
    test_files, run_parse = extract_test_info_from_patch(test_patch)

    return generate_eval_script(
        base_commit=base_commit,
        test_patch=test_patch,
        run_parse=run_parse,
        instance_id=instance_id,
        test_files=test_files,
        output_dir=output_dir
    )


def generate_eval_scripts_from_jsonl(
    jsonl_path: str,
    output_dir: Optional[str] = None,
    limit: Optional[int] = None
) -> List[str]:
    """
    从JSONL文件批量生成评估脚本

    Args:
        jsonl_path: JSONL文件路径
        output_dir: 输出目录
        limit: 限制生成的脚本数量

    Returns:
        生成的脚本文件路径列表
    """
    records = read_jsonl_file(jsonl_path)

    if limit:
        records = records[:limit]

    generated_scripts = []

    for i, record in enumerate(records):
        print(f"正在生成脚本 {i+1}/{len(records)}: {record.get('instance_id', 'unknown')}")
        try:
            script_path = generate_eval_script_from_record(record, output_dir)
            generated_scripts.append(script_path)
            print(f"  ✅ 成功生成: {script_path}")
        except Exception as e:
            print(f"  ❌ 生成失败: {e}")

    return generated_scripts


def generate_eval_script(
    base_commit: str,
    test_patch: str,
    run_parse: str,
    instance_id: str,
    test_files: list[str],
    output_dir: Optional[str] = None
) -> str:
    """
    生成评估脚本文件

    Args:
        base_commit: 基准提交哈希
        test_patch: 测试补丁内容（diff 格式）
        run_parse: 运行测试的命令
        instance_id: 实例ID，用于生成脚本文件名
        test_files: 测试文件路径列表
        output_dir: 输出目录，默认使用当前目录

    Returns:
        生成的脚本文件路径
    """
    if output_dir is None:
        output_dir = os.getcwd()

    script_filename = f"{instance_id}_eval.sh"
    script_path = os.path.join(output_dir, script_filename)

    # 构建脚本内容
    script_content = f"""#!/bin/bash
set -uxo pipefail
source /opt/miniconda3/bin/activate
conda activate testbed
cd /testbed
git config --global --add safe.directory /testbed
cd /testbed
git status
git show
git diff {base_commit}
source /opt/miniconda3/bin/activate
conda activate testbed
python -m pip install -e .
git checkout {base_commit} {' '.join(test_files)}
git apply -v - <<'EOF_{instance_id}'
{test_patch}
EOF_{instance_id}
"""

    # 写入文件
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(script_content)

    # 设置执行权限
    os.chmod(script_path, 0o755)

    return script_path


def main():
    """主函数，用于命令行调用"""
    import argparse

    parser = argparse.ArgumentParser(description='生成评估脚本')
    parser.add_argument('--jsonl-file', help='JSONL文件路径，从中读取参数')
    parser.add_argument('--limit', type=int, default=None, help='限制生成的脚本数量')
    parser.add_argument('--base-commit', help='基准提交哈希')
    parser.add_argument('--test-patch', help='测试补丁内容文件路径或内容')
    parser.add_argument('--run-parse', help='运行测试的命令')
    parser.add_argument('--instance-id', help='实例ID')
    parser.add_argument('--test-files', nargs='+', help='测试文件路径列表')
    parser.add_argument('--output-dir', default=None, help='输出目录')

    args = parser.parse_args()

    if args.jsonl_file:
        # 从JSONL文件批量生成
        print(f"从JSONL文件生成脚本: {args.jsonl_file}")
        if args.limit:
            print(f"限制数量: {args.limit}")

        generated_scripts = generate_eval_scripts_from_jsonl(
            jsonl_path=args.jsonl_file,
            output_dir=args.output_dir,
            limit=args.limit
        )

        print(f"\n📊 共生成 {len(generated_scripts)} 个评估脚本")
        for script in generated_scripts[:5]:  # 只显示前5个
            print(f"  - {script}")
        if len(generated_scripts) > 5:
            print(f"  ... 还有 {len(generated_scripts) - 5} 个脚本")

    else:
        # 单次生成，需要所有必要参数
        required_args = ['base_commit', 'test_patch', 'run_parse', 'instance_id', 'test_files']
        missing_args = [arg for arg in required_args if not getattr(args, arg.replace('-', '_'))]

        if missing_args:
            parser.error(f"缺少必要参数: {', '.join(missing_args)}")

        # 如果 test_patch 是文件路径，读取文件内容
        test_patch_content = args.test_patch
        if os.path.isfile(args.test_patch):
            with open(args.test_patch, 'r', encoding='utf-8') as f:
                test_patch_content = f.read()

        # 生成脚本
        script_path = generate_eval_script(
            base_commit=args.base_commit,
            test_patch=test_patch_content,
            run_parse=args.run_parse,
            instance_id=args.instance_id,
            test_files=args.test_files,
            output_dir=args.output_dir
        )

        print(f"生成的脚本文件: {script_path}")


if __name__ == "__main__":
    main()
