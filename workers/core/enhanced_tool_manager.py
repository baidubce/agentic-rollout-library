"""
Enhanced Tool Manager - 参考Claude-Code的src/tools.ts
负责工具管理、权限过滤、动态schema生成
"""

import logging
from typing import List, Dict, Any, Optional, Set
from functools import lru_cache

from workers.core.base_tool import AgenticBaseTool

logger = logging.getLogger(__name__)


class EnhancedToolManager:
    """增强的工具管理器，参考Claude-Code的tools.ts"""
    
    def __init__(self):
        self._all_tools: List[AgenticBaseTool] = []
        self._tool_registry: Dict[str, AgenticBaseTool] = {}
        self._read_only_tools: Set[str] = set()
        self._dangerous_tools: Set[str] = set()
        
    def register_tool(self, tool: AgenticBaseTool, is_read_only: bool = False, is_dangerous: bool = False):
        """注册工具到管理器"""
        self._all_tools.append(tool)
        tool_name = getattr(tool, 'name', tool.__class__.__name__)
        self._tool_registry[tool_name] = tool
        
        if is_read_only:
            self._read_only_tools.add(tool_name)
        if is_dangerous:
            self._dangerous_tools.add(tool_name)
            
        logger.debug(f"Registered tool: {tool_name} (read_only={is_read_only}, dangerous={is_dangerous})")
    
    def get_all_tools(self) -> List[AgenticBaseTool]:
        """获取所有已注册的工具"""
        return self._all_tools.copy()
    
    def get_tools_with_permissions(self, dangerous_skip_permissions: bool = False) -> List[AgenticBaseTool]:
        """
        根据权限获取可用工具，参考Claude-Code的getTools()
        """
        available_tools = []
        
        for tool in self._all_tools:
            tool_name = getattr(tool, 'name', tool.__class__.__name__)
            
            # 检查工具是否启用
            if hasattr(tool, 'is_enabled') and callable(tool.is_enabled):
                try:
                    if not tool.is_enabled():
                        logger.debug(f"Tool {tool_name} is disabled")
                        continue
                except Exception as e:
                    logger.warning(f"Error checking if tool {tool_name} is enabled: {e}")
                    continue
            
            # 检查危险工具权限
            if tool_name in self._dangerous_tools and not dangerous_skip_permissions:
                logger.debug(f"Skipping dangerous tool {tool_name} (permissions not skipped)")
                continue
            
            available_tools.append(tool)
        
        logger.info(f"Available tools: {[getattr(t, 'name', t.__class__.__name__) for t in available_tools]}")
        return available_tools
    
    def get_read_only_tools(self) -> List[AgenticBaseTool]:
        """获取只读工具列表，参考Claude-Code的getReadOnlyTools()"""
        read_only_tools = []
        
        for tool in self._all_tools:
            tool_name = getattr(tool, 'name', tool.__class__.__name__)
            
            # 检查是否为只读工具
            is_read_only = (
                tool_name in self._read_only_tools or
                (hasattr(tool, 'is_read_only') and callable(tool.is_read_only) and tool.is_read_only())
            )
            
            if is_read_only:
                # 检查工具是否启用
                if hasattr(tool, 'is_enabled') and callable(tool.is_enabled):
                    try:
                        if tool.is_enabled():
                            read_only_tools.append(tool)
                    except Exception as e:
                        logger.warning(f"Error checking if read-only tool {tool_name} is enabled: {e}")
                else:
                    read_only_tools.append(tool)
        
        return read_only_tools
    
    def generate_openai_function_schemas(
        self, 
        tools: Optional[List[AgenticBaseTool]] = None,
        context: Optional[Dict[str, Any]] = None,
        dangerous_skip_permissions: bool = False
    ) -> List[Dict[str, Any]]:
        """
        生成OpenAI function calling schemas，参考Claude-Code的toolSchemas生成
        """
        if tools is None:
            tools = self.get_tools_with_permissions(dangerous_skip_permissions)
        
        schemas = []
        
        for tool in tools:
            try:
                # 获取工具的详细描述
                description = self._get_tool_detailed_description(tool, context, dangerous_skip_permissions)
                
                # 获取工具的参数schema
                parameters_schema = self._get_tool_parameters_schema(tool)
                
                # 构建OpenAI function schema
                tool_name = getattr(tool, 'name', tool.__class__.__name__)
                
                schema = {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": description,
                        "parameters": parameters_schema
                    }
                }
                
                schemas.append(schema)
                logger.debug(f"Generated schema for tool: {tool_name}")
                
            except Exception as e:
                tool_name = getattr(tool, 'name', tool.__class__.__name__)
                logger.error(f"Failed to generate schema for tool {tool_name}: {e}")
                continue
        
        logger.info(f"Generated {len(schemas)} tool schemas")
        return schemas
    
    def _get_tool_detailed_description(
        self, 
        tool: AgenticBaseTool, 
        context: Optional[Dict[str, Any]] = None,
        dangerous_skip_permissions: bool = False
    ) -> str:
        """获取工具的详细描述"""
        
        # 尝试使用新的 get_detailed_prompt 方法
        if hasattr(tool, 'get_detailed_prompt') and callable(tool.get_detailed_prompt):
            try:
                # 传递上下文参数（如果工具支持的话）
                if context is not None:
                    try:
                        return tool.get_detailed_prompt(context=context)
                    except TypeError:
                        # 如果工具不支持context参数，则使用无参数版本
                        return tool.get_detailed_prompt()
                else:
                    return tool.get_detailed_prompt()
            except Exception as e:
                logger.warning(f"Error getting detailed prompt from tool {tool.__class__.__name__}: {e}")
        
        # 回退到传统的 get_openai_tool_schema 方法
        if hasattr(tool, 'get_openai_tool_schema') and callable(tool.get_openai_tool_schema):
            try:
                schema = tool.get_openai_tool_schema()
                return schema.get('function', {}).get('description', f"Tool: {tool.__class__.__name__}")
            except Exception as e:
                logger.warning(f"Error getting schema from tool {tool.__class__.__name__}: {e}")
        
        # 最终回退
        return f"Tool: {tool.__class__.__name__}"
    
    def _get_tool_parameters_schema(self, tool: AgenticBaseTool) -> Dict[str, Any]:
        """获取工具的参数schema"""
        
        # 尝试从 get_openai_tool_schema 获取
        if hasattr(tool, 'get_openai_tool_schema') and callable(tool.get_openai_tool_schema):
            try:
                schema = tool.get_openai_tool_schema()
                return schema.get('function', {}).get('parameters', {
                    "type": "object",
                    "properties": {},
                    "required": []
                })
            except Exception as e:
                logger.warning(f"Error getting parameters schema from tool {tool.__class__.__name__}: {e}")
        
        # 默认空schema
        return {
            "type": "object",
            "properties": {},
            "required": []
        }
    
    def find_tool_by_name(self, tool_name: str) -> Optional[AgenticBaseTool]:
        """根据名称查找工具"""
        return self._tool_registry.get(tool_name)
    
    def get_tool_names(self) -> List[str]:
        """获取所有工具名称"""
        return list(self._tool_registry.keys())
    
    def is_tool_dangerous(self, tool_name: str) -> bool:
        """检查工具是否为危险工具"""
        return tool_name in self._dangerous_tools
    
    def is_tool_read_only(self, tool_name: str) -> bool:
        """检查工具是否为只读工具"""
        return tool_name in self._read_only_tools


# 全局工具管理器实例
_enhanced_tool_manager = None


def get_enhanced_tool_manager() -> EnhancedToolManager:
    """获取增强工具管理器实例"""
    global _enhanced_tool_manager
    if _enhanced_tool_manager is None:
        _enhanced_tool_manager = EnhancedToolManager()
    return _enhanced_tool_manager


def register_cc_tools_to_manager(
    tool_instances: Dict[str, AgenticBaseTool],
    manager: Optional[EnhancedToolManager] = None
) -> EnhancedToolManager:
    """将CC Tools注册到管理器"""
    if manager is None:
        manager = get_enhanced_tool_manager()
    
    # 定义只读工具
    read_only_tools = {
        'cc_file_read', 'cc_ls', 'cc_glob', 'cc_grep', 
        'cc_nb_read', 'cc_memory_read', 'cc_think'
    }
    
    # 定义危险工具（需要权限的工具）
    dangerous_tools = {
        'cc_bash', 'cc_file_write', 'cc_file_edit', 
        'cc_nb_edit', 'cc_memory_write'
    }
    
    for tool_name, tool_instance in tool_instances.items():
        is_read_only = tool_name in read_only_tools
        is_dangerous = tool_name in dangerous_tools
        
        manager.register_tool(
            tool=tool_instance,
            is_read_only=is_read_only,
            is_dangerous=is_dangerous
        )
    
    logger.info(f"Registered {len(tool_instances)} CC Tools to manager")
    return manager



