#!/usr/bin/env python3
"""
移除.patch文件中关于pyproject.toml的diff的脚本

这个脚本会解析.patch文件，识别并移除所有与pyproject.toml相关的diff块，
保留其他文件的修改。
"""

import re
import sys
import os
from pathlib import Path
from typing import List, Tuple


def parse_patch_file(patch_content: str) -> List[Tuple[str, List[str]]]:
    """
    解析patch文件内容，返回文件块列表
    
    Args:
        patch_content: patch文件的完整内容
        
    Returns:
        包含(文件名, 行列表)元组的列表
    """
    # 匹配diff --git开头的行
    diff_pattern = r'^diff --git a/(.+?) b/(.+?)$'
    
    file_blocks = []
    current_file = None
    current_lines = []
    
    lines = patch_content.split('\n')
    
    for line in lines:
        # 检查是否是新的diff块开始
        match = re.match(diff_pattern, line)
        if match:
            # 保存前一个文件块
            if current_file is not None:
                file_blocks.append((current_file, current_lines))
            
            # 开始新的文件块
            current_file = match.group(1)  # 使用a/路径作为文件名
            current_lines = [line]
        else:
            # 继续添加到当前文件块
            if current_file is not None:
                current_lines.append(line)
    
    # 添加最后一个文件块
    if current_file is not None:
        file_blocks.append((current_file, current_lines))
    
    return file_blocks


def filter_pyproject_blocks(file_blocks: List[Tuple[str, List[str]]]) -> List[Tuple[str, List[str]]]:
    """
    过滤掉pyproject.toml相关的文件块
    
    Args:
        file_blocks: 文件块列表
        
    Returns:
        过滤后的文件块列表
    """
    filtered_blocks = []
    
    for filename, lines in file_blocks:
        # 跳过pyproject.toml文件
        if filename == 'pyproject.toml':
            print(f"🚫 跳过文件: {filename}")
            continue
        
        # 保留其他文件
        print(f"✅ 保留文件: {filename}")
        filtered_blocks.append((filename, lines))
    
    return filtered_blocks


def reconstruct_patch(file_blocks: List[Tuple[str, List[str]]]) -> str:
    """
    重新构建patch文件内容
    
    Args:
        file_blocks: 文件块列表
        
    Returns:
        重新构建的patch内容
    """
    patch_lines = []
    
    for filename, lines in file_blocks:
        patch_lines.extend(lines)
        # 在每个文件块之间添加空行
        if patch_lines and patch_lines[-1] != '':
            patch_lines.append('')
    
    return '\n'.join(patch_lines)


def process_patch_file(input_path: str, output_path: str = None) -> None:
    """
    处理patch文件，移除pyproject.toml相关的diff
    
    Args:
        input_path: 输入patch文件路径
        output_path: 输出patch文件路径，如果为None则覆盖原文件
    """
    input_file = Path(input_path)
    
    if not input_file.exists():
        print(f"❌ 错误: 文件 {input_path} 不存在")
        return
    
    print(f"📁 正在处理文件: {input_path}")
    
    # 读取patch文件内容
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            patch_content = f.read()
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")
        return
    
    # 解析patch文件
    print("🔍 正在解析patch文件...")
    file_blocks = parse_patch_file(patch_content)
    print(f"📊 找到 {len(file_blocks)} 个文件块")
    
    # 过滤pyproject.toml块
    print("🚫 正在过滤pyproject.toml相关的diff...")
    filtered_blocks = filter_pyproject_blocks(file_blocks)
    print(f"📊 过滤后剩余 {len(filtered_blocks)} 个文件块")
    
    # 重新构建patch内容
    print("🔧 正在重新构建patch文件...")
    new_patch_content = reconstruct_patch(filtered_blocks)
    
    # 确定输出路径
    if output_path is None:
        output_path = input_path
        print(f"💾 将覆盖原文件: {output_path}")
    else:
        print(f"💾 将保存到: {output_path}")
    
    # 写入新文件
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(new_patch_content)
        print(f"✅ 成功处理完成!")
        
        # 显示统计信息
        removed_count = len(file_blocks) - len(filtered_blocks)
        if removed_count > 0:
            print(f"📈 移除了 {removed_count} 个pyproject.toml相关的diff块")
        else:
            print("ℹ️  没有找到pyproject.toml相关的diff块")
            
    except Exception as e:
        print(f"❌ 写入文件失败: {e}")


def process_patch_folder(input_folder: str, output_folder: str) -> None:
    """
    处理文件夹中的所有patch文件
    
    Args:
        input_folder: 输入patch文件夹路径
        output_folder: 输出文件夹路径
    """
    input_path = Path(input_folder)
    output_path = Path(output_folder)
    
    if not input_path.exists():
        print(f"❌ 错误: 输入文件夹 {input_folder} 不存在")
        return
    
    if not input_path.is_dir():
        print(f"❌ 错误: {input_folder} 不是一个文件夹")
        return
    
    # 创建输出文件夹
    output_path.mkdir(parents=True, exist_ok=True)
    print(f"📁 创建输出文件夹: {output_folder}")
    
    # 查找所有.patch文件
    patch_files = list(input_path.glob("*.patch"))
    
    if not patch_files:
        print(f"❌ 在文件夹 {input_folder} 中没有找到.patch文件")
        return
    
    print(f"📊 找到 {len(patch_files)} 个patch文件")
    
    # 处理每个patch文件
    for i, patch_file in enumerate(patch_files, 1):
        print(f"\n{'='*50}")
        print(f"🔄 处理第 {i}/{len(patch_files)} 个文件: {patch_file.name}")
        print(f"{'='*50}")
        
        # 构建输出文件路径
        output_file = output_path / patch_file.name
        
        try:
            process_patch_file(str(patch_file), str(output_file))
        except Exception as e:
            print(f"❌ 处理文件 {patch_file.name} 时发生错误: {e}")
            continue
    
    print(f"\n🎉 所有文件处理完成！输出文件夹: {output_folder}")


def main():
    """主函数"""
    # 设置输入和输出文件夹路径
    input_folder = ".../patches"
    output_folder = "../patches_clean"
    
    print(f"📂 输入文件夹: {input_folder}")
    print(f"📂 输出文件夹: {output_folder}")
    
    try:
        process_patch_folder(input_folder, output_folder)
    except KeyboardInterrupt:
        print("\n⚠️  操作被用户中断")
    except Exception as e:
        print(f"❌ 处理过程中发生错误: {e}")


if __name__ == "__main__":
    main()
