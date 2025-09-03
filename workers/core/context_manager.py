"""
Context Manager - 参考Claude-Code的src/context.ts
负责项目上下文收集、Git状态监控、目录结构分析
"""

import os
import subprocess
import logging
from typing import Dict, Optional
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


class ContextManager:
    """上下文管理器，参考Claude-Code的context.ts"""
    
    def __init__(self, working_dir: Optional[str] = None):
        self.working_dir = working_dir or os.getcwd()
    
    @lru_cache(maxsize=1)
    def get_project_context(self) -> Dict[str, str]:
        """
        获取项目完整上下文，参考Claude-Code的getContext()
        包含Git状态、目录结构、README等信息
        """
        context = {}
        
        try:
            # 获取Git上下文
            git_context = self.get_git_context()
            if git_context:
                context.update(git_context)
            
            # 获取目录结构
            directory_structure = self._get_directory_structure()
            if directory_structure:
                context['directoryStructure'] = directory_structure
            
            # 获取README内容
            readme_content = self._get_readme_content()
            if readme_content:
                context['readme'] = readme_content
            
            # 获取配置文件内容
            config_files = self._get_config_files()
            if config_files:
                context.update(config_files)
                
            logger.info(f"Collected project context with {len(context)} sections")
            
        except Exception as e:
            logger.error(f"Error collecting project context: {e}")
        
        return context
    
    @lru_cache(maxsize=1)
    def get_git_context(self) -> Dict[str, str]:
        """
        获取Git上下文信息，参考Claude-Code的getGitStatus()
        """
        context = {}
        
        try:
            if not self._is_git_repo():
                return context
            
            # 获取当前分支
            current_branch = self._run_git_command(['branch', '--show-current'])
            
            # 获取主分支
            try:
                main_branch = self._run_git_command(['rev-parse', '--abbrev-ref', 'origin/HEAD'])
                main_branch = main_branch.replace('origin/', '').strip()
            except:
                main_branch = 'main'  # 默认主分支
            
            # 获取Git状态
            git_status = self._run_git_command(['status', '--short'])
            
            # 获取最近提交
            recent_commits = self._run_git_command(['log', '--oneline', '-n', '5'])
            
            # 获取用户最近提交
            try:
                user_email = self._run_git_command(['config', 'user.email'])
                user_commits = self._run_git_command([
                    'log', '--oneline', '-n', '5', '--author', user_email
                ])
            except:
                user_commits = '(no recent commits)'
            
            # 构建Git上下文描述
            git_status_text = git_status.strip() or '(clean)'
            
            # 处理过长的状态信息
            status_lines = git_status_text.split('\n')
            if len(status_lines) > 200:
                truncated_status = '\n'.join(status_lines[:200])
                truncated_status += '\n... (truncated because there are more than 200 lines. Use cc_bash with "git status" for full output)'
                git_status_text = truncated_status
            
            git_info = f"""This is the git status at the start of the conversation. Note that this status is a snapshot in time, and will not update during the conversation.
Current branch: {current_branch}

Main branch (you will usually use this for PRs): {main_branch}

Status:
{git_status_text}

Recent commits:
{recent_commits}

Your recent commits:
{user_commits}"""
            
            context['gitStatus'] = git_info
            logger.debug("Collected Git context information")
            
        except Exception as e:
            logger.warning(f"Failed to get Git context: {e}")
        
        return context
    
    def get_environment_context(self) -> Dict[str, str]:
        """获取环境上下文信息"""
        context = {}
        
        try:
            # Kubernetes环境信息
            if os.getenv('KUBERNETES_SERVICE_HOST'):
                context['k8sEnvironment'] = 'Running in Kubernetes environment'
                
                # Pod信息
                pod_name = os.getenv('HOSTNAME', 'unknown')
                namespace = os.getenv('POD_NAMESPACE', 'default')
                context['podInfo'] = f"Pod: {pod_name}, Namespace: {namespace}"
            
            # 容器环境信息
            if os.path.exists('/.dockerenv'):
                context['containerEnvironment'] = 'Running in Docker container'
            
            # 工作目录信息
            context['workingDirectory'] = self.working_dir
            
            # 环境变量（安全的）
            safe_env_vars = {}
            for key in ['PATH', 'LANG', 'LC_ALL', 'TERM']:
                if key in os.environ:
                    safe_env_vars[key] = os.environ[key]
            
            if safe_env_vars:
                context['environmentVariables'] = str(safe_env_vars)
                
        except Exception as e:
            logger.warning(f"Failed to get environment context: {e}")
        
        return context
    
    def _is_git_repo(self) -> bool:
        """检查是否为Git仓库"""
        try:
            subprocess.run(
                ['git', 'rev-parse', '--git-dir'],
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=5,
                check=True
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def _run_git_command(self, args: list, timeout: int = 10) -> str:
        """运行Git命令并返回输出"""
        try:
            result = subprocess.run(
                ['git'] + args,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Git command failed: git {' '.join(args)}: {e}")
            raise
    
    def _get_directory_structure(self) -> Optional[str]:
        """获取目录结构快照，参考Claude-Code的getDirectoryStructure()"""
        try:
            # 使用简单的ls命令获取目录结构
            result = subprocess.run(
                ['ls', '-la'],
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                structure = f"""Below is a snapshot of this project's file structure at the start of the conversation. This snapshot will NOT update during the conversation.

{result.stdout}"""
                return structure
        except Exception as e:
            logger.warning(f"Failed to get directory structure: {e}")
        
        return None
    
    def _get_readme_content(self) -> Optional[str]:
        """获取README内容，参考Claude-Code的getReadme()"""
        readme_files = ['README.md', 'README.txt', 'README.rst', 'README']
        
        for readme_file in readme_files:
            readme_path = Path(self.working_dir) / readme_file
            try:
                if readme_path.exists() and readme_path.is_file():
                    content = readme_path.read_text(encoding='utf-8', errors='ignore')
                    # 限制README内容长度
                    if len(content) > 5000:
                        content = content[:5000] + '\n... (truncated)'
                    return content
            except Exception as e:
                logger.warning(f"Failed to read {readme_file}: {e}")
                continue
        
        return None
    
    def _get_config_files(self) -> Dict[str, str]:
        """获取常见配置文件内容"""
        config_files = {}
        
        # 常见配置文件列表
        config_filenames = [
            'pyproject.toml', 'requirements.txt', 'setup.py',
            'package.json', 'Dockerfile', '.gitignore',
            'Makefile', 'docker-compose.yml'
        ]
        
        for filename in config_filenames:
            file_path = Path(self.working_dir) / filename
            try:
                if file_path.exists() and file_path.is_file():
                    content = file_path.read_text(encoding='utf-8', errors='ignore')
                    # 限制配置文件内容长度
                    if len(content) > 2000:
                        content = content[:2000] + '\n... (truncated)'
                    config_files[f'configFile_{filename.replace(".", "_")}'] = content
            except Exception as e:
                logger.warning(f"Failed to read config file {filename}: {e}")
                continue
        
        return config_files
    
    def set_context(self, key: str, value: str):
        """设置自定义上下文"""
        # 清除缓存以便重新生成上下文
        self.get_project_context.cache_clear()
        # 这里可以扩展为持久化存储
        logger.info(f"Context updated: {key}")
    
    def remove_context(self, key: str):
        """移除自定义上下文"""
        # 清除缓存以便重新生成上下文
        self.get_project_context.cache_clear()
        logger.info(f"Context removed: {key}")


# 全局上下文管理器实例
_context_manager = None


def get_context_manager(working_dir: Optional[str] = None) -> ContextManager:
    """获取上下文管理器实例"""
    global _context_manager
    if _context_manager is None:
        _context_manager = ContextManager(working_dir)
    return _context_manager



