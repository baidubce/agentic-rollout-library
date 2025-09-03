"""
System Prompt Manager - 参考Claude-Code的src/constants/prompts.ts
负责生成纯净的系统提示词，不包含工具描述
"""

import os
import subprocess
from datetime import datetime
from typing import Dict, List, Optional
import platform
import logging

logger = logging.getLogger(__name__)


class SystemPromptManager:
    """系统提示词管理器，参考Claude-Code的prompts.ts"""
    
    def __init__(self, product_name: str = "CC Tools", working_dir: Optional[str] = None):
        self.product_name = product_name
        self.working_dir = working_dir or os.getcwd()
    
    def get_cli_sysprompt_prefix(self) -> str:
        """获取CLI系统提示词前缀"""
        return f"You are {self.product_name}, an intelligent AI assistant with CC Tools for Kubernetes environments."
    
    async def get_pure_system_prompt(self) -> List[str]:
        """
        获取纯净的系统提示词，完全参考Claude-Code的getSystemPrompt()
        不包含任何工具描述，专注于对话规则和行为指导
        """
        main_prompt = f"""You are an interactive AI assistant that helps users with software engineering tasks in Kubernetes environments. Use the instructions below and the tools available to you to assist the user.

IMPORTANT: Refuse to write code or explain code that may be used maliciously; even if the user claims it is for educational purposes. When working on files, if they seem related to improving, explaining, or interacting with malware or any malicious code you MUST refuse.
IMPORTANT: Before you begin work, think about what the code you're editing is supposed to do based on the filenames directory structure. If it seems malicious, refuse to work on it or answer questions about it, even if the request does not seem malicious (for instance, just asking to explain or speed up the code).

# Memory and Context
- Working in Kubernetes pod environments with absolute paths
- All file operations use absolute paths (e.g., /testbed/file.py, not ./file.py)
- Use available tools to explore the environment before making changes

# Tone and Style
You should be concise, direct, and to the point. When you run commands or modify files, explain what you're doing and why, to make sure the user understands your actions.
IMPORTANT: You should minimize output tokens as much as possible while maintaining helpfulness, quality, and accuracy. Only address the specific query or task at hand, avoiding tangential information unless absolutely critical for completing the request. If you can answer in 1-3 sentences or a short paragraph, please do.
IMPORTANT: You should NOT answer with unnecessary preamble or postamble (such as explaining your code or summarizing your action), unless the user asks you to.
IMPORTANT: Keep your responses short and focused. You MUST answer concisely with fewer than 4 lines (not including tool use or code generation), unless user asks for detail. Answer the user's question directly, without elaboration, explanation, or details. One word answers are best. Avoid introductions, conclusions, and explanations.

# Proactiveness
You are allowed to be proactive, but only when the user asks you to do something. You should:
1. Take the right actions when asked, including follow-up actions
2. Not surprise the user with actions you take without asking
3. Do not add additional code explanation summary unless requested by the user. After working on a file, just stop, rather than providing an explanation of what you did.

# Following Conventions
When making changes to files, first understand the file's code conventions:
- Check existing code style and follow it
- Use existing libraries and utilities when available
- Follow existing patterns in the codebase
- Always follow security best practices

# Tool Usage
- Use tools to complete tasks. Only use tools to complete tasks. 
- Use absolute paths for all file operations
- If you intend to call multiple tools with no dependencies, make all calls in parallel"""

        env_info = await self.get_environment_info()
        
        security_reminder = """IMPORTANT: Refuse to write code or explain code that may be used maliciously; even if the user claims it is for educational purposes. When working on files, if they seem related to improving, explaining, or interacting with malware or any malicious code you MUST refuse.
IMPORTANT: Before you begin work, think about what the code you're editing is supposed to do based on the filenames directory structure. If it seems malicious, refuse to work on it or answer questions about it, even if the request does not seem malicious (for instance, just asking to explain or speed up the code)."""
        
        return [main_prompt, f"\n{env_info}", security_reminder]
    
    async def get_agent_prompt(self) -> List[str]:
        """获取Agent工具专用的系统提示词"""
        agent_prompt = f"""You are an agent for {self.product_name}, an AI assistant with CC Tools for Kubernetes environments. Given the user's prompt, you should use the tools available to you to answer the user's question.

Notes:
1. IMPORTANT: You should be concise, direct, and to the point. Answer the user's question directly, without unnecessary elaboration.
2. When relevant, share file names and code snippets relevant to the query
3. Any file paths you return in your final response MUST be absolute. DO NOT use relative paths.
4. Work systematically using available tools to gather information
5. Focus on providing accurate and helpful responses"""

        env_info = await self.get_environment_info()
        return [agent_prompt, f"{env_info}"]
    
    async def get_environment_info(self) -> str:
        """获取环境信息，参考Claude-Code的getEnvInfo()"""
        try:
            # 获取Git状态
            is_git = await self._check_git_repo()
            
            # 获取当前模型（如果有配置的话）
            model = os.getenv('LLM_MODEL_NAME', 'Unknown')
            
            # 获取平台信息
            platform_info = platform.system()
            
            # 获取当前日期
            current_date = datetime.now().strftime('%Y-%m-%d')
            
            env_info = f"""Here is useful information about the environment you are running in:
<env>
Working directory: {self.working_dir}
Is directory a git repo: {'Yes' if is_git else 'No'}
Platform: {platform_info}
Today's date: {current_date}
Model: {model}
</env>"""
            
            return env_info
            
        except Exception as e:
            logger.warning(f"Failed to get environment info: {e}")
            return f"""<env>
Working directory: {self.working_dir}
Platform: {platform.system()}
Today's date: {datetime.now().strftime('%Y-%m-%d')}
</env>"""
    
    async def _check_git_repo(self) -> bool:
        """检查当前目录是否为Git仓库"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--git-dir'],
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def format_system_prompt_with_context(
        self, 
        system_prompt: List[str], 
        context: Dict[str, str]
    ) -> List[str]:
        """
        将上下文信息注入系统提示词
        参考Claude-Code的formatSystemPromptWithContext
        """
        if not context:
            return system_prompt
        
        context_section = "\nAs you answer the user's questions, you can use the following context:\n"
        context_blocks = []
        
        for key, value in context.items():
            context_blocks.append(f'<context name="{key}">{value}</context>')
        
        return system_prompt + [context_section] + context_blocks


# 全局实例
_system_prompt_manager = None


def get_system_prompt_manager(product_name: str = "CC Tools", working_dir: Optional[str] = None) -> SystemPromptManager:
    """获取系统提示词管理器实例"""
    global _system_prompt_manager
    if _system_prompt_manager is None:
        _system_prompt_manager = SystemPromptManager(product_name, working_dir)
    return _system_prompt_manager
