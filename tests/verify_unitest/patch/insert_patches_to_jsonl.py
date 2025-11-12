#!/usr/bin/env python3
"""
处理swe_bench数据集，将patches文件夹中的.patch文件内容添加到对应的jsonl记录中
"""

import json
import os
import glob
from pathlib import Path
from typing import Dict, List, Optional
import logging

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def extract_instance_id_from_patch_filename(filename: str) -> Optional[str]:
    """
    从patch文件名中提取instance_id
    
    Args:
        filename: patch文件名，例如: "astropy__astropy-6938_20250827_003410.patch"
    
    Returns:
        instance_id，例如: "astropy__astropy-6938"
    """
    # 移除.patch扩展名
    name_without_ext = filename.replace('.patch', '')
    
    # 查找最后一个下划线和时间戳的分隔点
    # 时间戳格式通常是: YYYYMMDD_HHMMSS
    parts = name_without_ext.split('_')
    
    if len(parts) < 3:
        logger.warning(f"无法解析文件名: {filename}")
        return None
    
    # 假设最后两部分是时间戳 (YYYYMMDD_HHMMSS)
    # 前面的部分就是instance_id
    instance_id = '_'.join(parts[:-2])
    
    return instance_id


def load_patches_from_directory(patches_dir: str) -> Dict[str, str]:
    """
    从patches目录加载所有.patch文件，建立instance_id到patch内容的映射
    
    Args:
        patches_dir: patches文件夹路径
    
    Returns:
        Dict[instance_id, patch_content]
    """
    patches_dict = {}
    patches_path = Path(patches_dir)
    
    if not patches_path.exists():
        logger.error(f"Patches目录不存在: {patches_dir}")
        return patches_dict
    
    # 查找所有.patch文件
    patch_files = glob.glob(str(patches_path / "*.patch"))
    logger.info(f"找到 {len(patch_files)} 个patch文件")
    
    for patch_file in patch_files:
        try:
            # 从文件名提取instance_id
            filename = Path(patch_file).name
            instance_id = extract_instance_id_from_patch_filename(filename)
            
            if instance_id is None:
                continue
            
            # 读取patch文件内容
            with open(patch_file, 'r', encoding='utf-8') as f:
                patch_content = f.read()
            
            patches_dict[instance_id] = patch_content
            logger.debug(f"加载patch: {instance_id} -> {len(patch_content)} 字符")
            
        except Exception as e:
            logger.error(f"处理patch文件 {patch_file} 时出错: {e}")
    
    logger.info(f"成功加载 {len(patches_dict)} 个patch文件")
    return patches_dict


def process_jsonl_with_patches(jsonl_path: str, patches_dict: Dict[str, str], output_path: str):
    """
    处理jsonl文件，为每条记录添加ours_patch字段
    
    Args:
        jsonl_path: 输入jsonl文件路径
        patches_dict: instance_id到patch内容的映射
        output_path: 输出jsonl文件路径
    """
    processed_count = 0
    added_patch_count = 0
    missing_patch_count = 0
    
    with open(jsonl_path, 'r', encoding='utf-8') as infile, \
         open(output_path, 'w', encoding='utf-8') as outfile:
        
        for line_num, line in enumerate(infile, 1):
            try:
                # 解析JSON行
                data = json.loads(line.strip())
                processed_count += 1
                
                # 获取instance_id
                instance_id = data.get('instance_id')
                if instance_id is None:
                    logger.warning(f"第{line_num}行缺少instance_id字段")
                    # 仍然输出原始数据
                    outfile.write(line)
                    continue
                
                # 查找对应的patch
                if instance_id in patches_dict:
                    data['ours_patch'] = patches_dict[instance_id]
                    added_patch_count += 1
                    logger.debug(f"为 {instance_id} 添加patch")
                else:
                    data['ours_patch'] = None
                    missing_patch_count += 1
                    logger.debug(f"未找到 {instance_id} 的patch")
                
                # 写入更新后的数据
                outfile.write(json.dumps(data, ensure_ascii=False) + '\n')
                
            except json.JSONDecodeError as e:
                logger.error(f"第{line_num}行JSON解析错误: {e}")
                # 跳过错误的行
                continue
            except Exception as e:
                logger.error(f"处理第{line_num}行时出错: {e}")
                # 跳过错误的行
                continue
    
    logger.info(f"处理完成:")
    logger.info(f"  总记录数: {processed_count}")
    logger.info(f"  添加patch的记录数: {added_patch_count}")
    logger.info(f"  未找到patch的记录数: {missing_patch_count}")


def main():
    """主函数"""
    # 配置路径
    jsonl_path = "../test-00000-of-00001-with-images.jsonl"
    patches_dir = "../patch_clean_mini"
    
    # 生成输出文件路径
    output_dir = Path(jsonl_path).parent
    output_filename = Path(jsonl_path).stem + "_with_patches.jsonl"
    output_path = output_dir / output_filename
    
    logger.info("开始处理swe_bench数据集...")
    logger.info(f"输入文件: {jsonl_path}")
    logger.info(f"Patches目录: {patches_dir}")
    logger.info(f"输出文件: {output_path}")
    
    # 检查输入文件是否存在
    if not os.path.exists(jsonl_path):
        logger.error(f"输入文件不存在: {jsonl_path}")
        return
    
    # 加载patches
    patches_dict = load_patches_from_directory(patches_dir)
    
    if not patches_dict:
        logger.warning("没有找到任何patch文件，将创建只包含None值的ours_patch字段")
    
    # 处理jsonl文件
    process_jsonl_with_patches(jsonl_path, patches_dict, str(output_path))
    
    logger.info(f"处理完成！输出文件: {output_path}")


if __name__ == "__main__":
    main()
