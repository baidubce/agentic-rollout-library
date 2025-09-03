"""
Config Manager - 参考Claude-Code的src/utils/config.ts
负责配置文件管理、权限设置、用户偏好
"""

import os
import json
import logging
from typing import Dict, List, Any, Optional, Set
from pathlib import Path
from dataclasses import dataclass, asdict
from functools import lru_cache

logger = logging.getLogger(__name__)


@dataclass
class ProjectConfig:
    """项目配置，参考Claude-Code的ProjectConfig"""
    allowed_tools: List[str]
    context: Dict[str, str]
    history: List[str]
    dont_crawl_directory: bool = False
    enable_architect_tool: bool = False
    dangerous_skip_permissions: bool = False
    last_api_duration: Optional[float] = None
    last_cost: Optional[float] = None
    last_session_id: Optional[str] = None
    has_completed_project_onboarding: bool = False


@dataclass
class GlobalConfig:
    """全局配置，参考Claude-Code的GlobalConfig"""
    primary_api_key: Optional[str] = None
    custom_api_key_responses: Dict[str, List[str]] = None
    theme: str = "dark"
    auto_update: bool = True
    projects: Dict[str, ProjectConfig] = None
    
    def __post_init__(self):
        if self.custom_api_key_responses is None:
            self.custom_api_key_responses = {"approved": [], "rejected": []}
        if self.projects is None:
            self.projects = {}


class ConfigManager:
    """配置管理器，参考Claude-Code的config.ts"""
    
    def __init__(self, working_dir: Optional[str] = None):
        self.working_dir = working_dir or os.getcwd()
        self.global_config_file = Path.home() / ".cc_tools" / "config.json"
        self.project_config_file = Path(self.working_dir) / ".cc_tools_config.json"
        
        # 确保配置目录存在
        self.global_config_file.parent.mkdir(exist_ok=True)
    
    @lru_cache(maxsize=1)
    def get_global_config(self) -> GlobalConfig:
        """获取全局配置"""
        try:
            if self.global_config_file.exists():
                with open(self.global_config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return GlobalConfig(**data)
        except Exception as e:
            logger.warning(f"Failed to load global config: {e}")
        
        # 返回默认配置
        return GlobalConfig()
    
    def save_global_config(self, config: GlobalConfig):
        """保存全局配置"""
        try:
            with open(self.global_config_file, 'w', encoding='utf-8') as f:
                json.dump(asdict(config), f, indent=2, ensure_ascii=False)
            # 清除缓存
            self.get_global_config.cache_clear()
            logger.debug("Global config saved")
        except Exception as e:
            logger.error(f"Failed to save global config: {e}")
    
    @lru_cache(maxsize=1)
    def get_project_config(self) -> ProjectConfig:
        """获取当前项目配置"""
        try:
            if self.project_config_file.exists():
                with open(self.project_config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return ProjectConfig(**data)
        except Exception as e:
            logger.warning(f"Failed to load project config: {e}")
        
        # 返回默认项目配置
        return ProjectConfig(
            allowed_tools=[],
            context={},
            history=[]
        )
    
    def save_project_config(self, config: ProjectConfig):
        """保存项目配置"""
        try:
            with open(self.project_config_file, 'w', encoding='utf-8') as f:
                json.dump(asdict(config), f, indent=2, ensure_ascii=False)
            # 清除缓存
            self.get_project_config.cache_clear()
            logger.debug("Project config saved")
        except Exception as e:
            logger.error(f"Failed to save project config: {e}")
    
    def get_tool_permissions(self) -> Dict[str, bool]:
        """获取工具权限配置"""
        project_config = self.get_project_config()
        
        # 如果有明确的允许工具列表，使用它
        if project_config.allowed_tools:
            permissions = {}
            for tool_name in project_config.allowed_tools:
                permissions[tool_name] = True
            return permissions
        
        # 默认权限配置
        default_permissions = {
            # 只读工具默认允许
            'cc_file_read': True,
            'cc_ls': True,
            'cc_glob': True,
            'cc_grep': True,
            'cc_nb_read': True,
            'cc_memory_read': True,
            'cc_think': True,
            
            # 写入工具需要明确权限
            'cc_file_write': False,
            'cc_file_edit': False,
            'cc_bash': False,
            'cc_nb_edit': False,
            'cc_memory_write': False,
            
            # 特殊工具
            'cc_agent': True,
            'cc_architect': project_config.enable_architect_tool,
            'cc_mcp': False,
            'cc_sticker': True,
        }
        
        return default_permissions
    
    def get_user_preferences(self) -> Dict[str, Any]:
        """获取用户偏好设置"""
        global_config = self.get_global_config()
        
        return {
            'theme': global_config.theme,
            'auto_update': global_config.auto_update,
            'api_key_configured': bool(global_config.primary_api_key or self.get_api_key_from_env()),
        }
    
    def should_skip_permissions(self) -> bool:
        """是否跳过权限检查"""
        project_config = self.get_project_config()
        
        # 检查项目配置
        if project_config.dangerous_skip_permissions:
            return True
        
        # 检查环境变量
        if os.getenv('CC_TOOLS_SKIP_PERMISSIONS', '').lower() in ('true', '1', 'yes'):
            return True
        
        return False
    
    def get_api_key_from_env(self) -> Optional[str]:
        """从环境变量获取API密钥"""
        return os.getenv('LLM_API_KEY') or os.getenv('OPENAI_API_KEY') or os.getenv('ANTHROPIC_API_KEY')
    
    def get_effective_api_key(self) -> Optional[str]:
        """获取有效的API密钥"""
        # 首先尝试环境变量
        env_key = self.get_api_key_from_env()
        if env_key:
            return env_key
        
        # 然后尝试全局配置
        global_config = self.get_global_config()
        return global_config.primary_api_key
    
    def set_tool_permission(self, tool_name: str, allowed: bool):
        """设置工具权限"""
        project_config = self.get_project_config()
        
        if allowed:
            if tool_name not in project_config.allowed_tools:
                project_config.allowed_tools.append(tool_name)
        else:
            if tool_name in project_config.allowed_tools:
                project_config.allowed_tools.remove(tool_name)
        
        self.save_project_config(project_config)
        logger.info(f"Tool permission updated: {tool_name} = {allowed}")
    
    def enable_dangerous_mode(self, enabled: bool = True):
        """启用/禁用危险模式（跳过权限检查）"""
        project_config = self.get_project_config()
        project_config.dangerous_skip_permissions = enabled
        self.save_project_config(project_config)
        logger.warning(f"Dangerous mode {'enabled' if enabled else 'disabled'}")
    
    def set_user_preference(self, key: str, value: Any):
        """设置用户偏好"""
        global_config = self.get_global_config()
        
        if key == 'theme':
            global_config.theme = value
        elif key == 'auto_update':
            global_config.auto_update = value
        elif key == 'primary_api_key':
            global_config.primary_api_key = value
        
        self.save_global_config(global_config)
        logger.info(f"User preference updated: {key}")
    
    def add_to_history(self, command: str):
        """添加到历史记录"""
        project_config = self.get_project_config()
        
        # 避免重复
        if command in project_config.history:
            project_config.history.remove(command)
        
        # 添加到开头
        project_config.history.insert(0, command)
        
        # 限制历史记录长度
        if len(project_config.history) > 100:
            project_config.history = project_config.history[:100]
        
        self.save_project_config(project_config)
    
    def get_history(self) -> List[str]:
        """获取历史记录"""
        project_config = self.get_project_config()
        return project_config.history.copy()
    
    def set_context(self, key: str, value: str):
        """设置上下文"""
        project_config = self.get_project_config()
        project_config.context[key] = value
        self.save_project_config(project_config)
    
    def get_context(self) -> Dict[str, str]:
        """获取上下文"""
        project_config = self.get_project_config()
        return project_config.context.copy()
    
    def normalize_api_key(self, api_key: str) -> str:
        """标准化API密钥（只保留后20位用于配置）"""
        return api_key[-20:] if len(api_key) > 20 else api_key
    
    def is_api_key_approved(self, api_key: str) -> bool:
        """检查API密钥是否已批准"""
        global_config = self.get_global_config()
        normalized_key = self.normalize_api_key(api_key)
        
        approved_keys = global_config.custom_api_key_responses.get('approved', [])
        return normalized_key in approved_keys
    
    def approve_api_key(self, api_key: str):
        """批准API密钥"""
        global_config = self.get_global_config()
        normalized_key = self.normalize_api_key(api_key)
        
        if normalized_key not in global_config.custom_api_key_responses['approved']:
            global_config.custom_api_key_responses['approved'].append(normalized_key)
            self.save_global_config(global_config)
            logger.info("API key approved")


# 全局配置管理器实例
_config_manager = None


def get_config_manager(working_dir: Optional[str] = None) -> ConfigManager:
    """获取配置管理器实例"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(working_dir)
    return _config_manager



