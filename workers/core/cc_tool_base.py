"""
CC Tools Base - 简化的CC Tools基类
直接继承AgenticBaseTool + PromptMixin，提供Claude-Code风格的prompt功能
"""

from typing import Dict, Any, Optional
import importlib

from workers.core.base_tool import AgenticBaseTool
from workers.core.tool_schemas import OpenAIFunctionToolSchema


class PromptMixin:
    """
    Prompt混入类，为工具提供从prompt.py文件加载描述的功能
    """
    
    def _load_prompt_from_module(self) -> tuple[str, str]:
        """
        从prompt.py模块加载DESCRIPTION和PROMPT
        
        Returns:
            (description, prompt) tuple
        """
        try:
            # 动态导入prompt模块
            module_path = f"{self.__module__.rsplit('.', 1)[0]}.prompt"
            prompt_module = importlib.import_module(module_path)
            
            description = getattr(prompt_module, 'DESCRIPTION', '')
            prompt = getattr(prompt_module, 'PROMPT', description)
            
            return description, prompt
            
        except (ImportError, AttributeError) as e:
            # 如果无法加载prompt模块，返回默认值
            return f"Tool: {self.__class__.__name__}", f"Tool: {self.__class__.__name__}"
    
    def get_detailed_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        获取详细的工具描述，用于LLM的function calling
        参考Claude-Code工具的prompt()方法
        """
        _, prompt = self._load_prompt_from_module()
        
        # 如果有上下文，可以进行定制
        if context:
            prompt = f"{prompt}\n\nContext: {context}"
        
        return prompt
    
    def get_brief_description(self) -> str:
        """获取简短的工具描述"""
        description, _ = self._load_prompt_from_module()
        return description
    
    def is_read_only(self) -> bool:
        """
        检查工具是否为只读工具
        参考Claude-Code工具的isReadOnly()方法
        
        Returns:
            True if the tool only reads data, False if it can modify data
        """
        # 默认工具为非只读，子类可以重写
        return False
    
    def is_enabled(self) -> bool:
        """
        检查工具是否启用
        参考Claude-Code工具的isEnabled()方法
        """
        return True
    
    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        """
        检查工具是否需要权限确认
        参考Claude-Code工具的needsPermissions()方法
        """
        return not self.is_read_only()


class CCToolBase(AgenticBaseTool, PromptMixin):
    """
    CC Tools的统一基类
    直接继承AgenticBaseTool，添加PromptMixin功能
    
    优势：
    1. 简洁的继承链：AgenticBaseTool → CCToolBase
    2. 复用成熟的K8s功能（原始工具已有）
    3. 获得Claude-Code风格的prompt功能
    4. 保持VERL完全兼容
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None, tool_schema: Optional[OpenAIFunctionToolSchema] = None):
        super().__init__(config, tool_schema)
        
        # 设置工具名称（如果未设置）
        if not hasattr(self, 'name') or not self.name:
            class_name = self.__class__.__name__
            if class_name.startswith('K8s') and class_name.endswith('Tool'):
                # 从K8sFileReadTool -> cc_file_read
                tool_name = class_name[3:-4]  # 移除K8s前缀和Tool后缀
                # 转换驼峰命名为下划线
                import re
                tool_name = re.sub('([A-Z])', r'_\1', tool_name).lower().strip('_')
                self.name = f"cc_{tool_name}"
            else:
                self.name = class_name.lower().replace('tool', '')
    
    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        """
        获取OpenAI工具schema，使用Claude-Code风格的详细描述
        重写基类方法以使用prompt.py中的描述
        """
        # 如果已经有schema，直接返回
        if hasattr(self, 'tool_schema') and self.tool_schema is not None:
            return self.tool_schema
        
        # 否则使用prompt.py中的描述创建schema
        description = self.get_detailed_prompt()
        
        # 获取参数schema（由子类实现）
        parameters = self._get_parameters_schema()
        
        from workers.core.tool_schemas import create_openai_tool_schema
        
        return create_openai_tool_schema(
            name=self.name or self.__class__.__name__.lower().replace('tool', ''),
            description=description,
            parameters=parameters
        )
    
    def _get_parameters_schema(self) -> Dict[str, Any]:
        """
        获取参数schema，子类需要重写此方法
        
        Returns:
            OpenAI function calling parameters schema
        """
        return {
            "type": "object",
            "properties": {},
            "required": []
        }
    
    def get_description(self) -> str:
        """
        重写基类的get_description方法，使用Claude-Code风格的描述
        这个方法用于系统提示词中的工具描述
        """
        return self.get_detailed_prompt()
