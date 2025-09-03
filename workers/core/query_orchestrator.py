"""
Query Orchestrator - 参考Claude-Code的src/query.ts
负责查询编排、工具调用循环、错误处理
"""

import json
import logging
import asyncio
from typing import Dict, List, Any, Optional, AsyncGenerator, Callable
from dataclasses import dataclass

from workers.core.enhanced_tool_manager import EnhancedToolManager
from workers.core.system_prompts import SystemPromptManager
from workers.core.context_manager import ContextManager
from workers.core.config_manager import ConfigManager
from workers.utils.llm_client import LLMAPIClient
from workers.core.base_tool import AgenticBaseTool

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """消息结构"""
    role: str  # 'system', 'user', 'assistant', 'tool'
    content: str
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass
class ToolUseContext:
    """工具使用上下文"""
    tools: List[AgenticBaseTool]
    dangerous_skip_permissions: bool = False
    max_thinking_tokens: int = 0
    verbose: bool = False
    timeout: int = 30


class QueryOrchestrator:
    """查询编排器，参考Claude-Code的query.ts"""
    
    def __init__(
        self,
        llm_client: LLMAPIClient,
        tool_manager: EnhancedToolManager,
        system_prompt_manager: SystemPromptManager,
        context_manager: ContextManager,
        config_manager: ConfigManager
    ):
        self.llm_client = llm_client
        self.tool_manager = tool_manager
        self.system_prompt_manager = system_prompt_manager
        self.context_manager = context_manager
        self.config_manager = config_manager
    
    async def query_with_tools(
        self,
        messages: List[Message],
        context: Optional[Dict[str, str]] = None,
        tool_use_context: Optional[ToolUseContext] = None,
        can_use_tool: Optional[Callable[[str, Dict], bool]] = None
    ) -> AsyncGenerator[Message, None]:
        """
        主查询循环，参考Claude-Code的query()函数
        """
        try:
            # 获取系统提示词
            system_prompt = await self.system_prompt_manager.get_pure_system_prompt()
            
            # 获取上下文信息
            if context is None:
                context = self.context_manager.get_project_context()
            
            # 格式化系统提示词
            full_system_prompt = self.system_prompt_manager.format_system_prompt_with_context(
                system_prompt, context
            )
            
            # 获取工具配置
            if tool_use_context is None:
                available_tools = self.tool_manager.get_tools_with_permissions(
                    self.config_manager.should_skip_permissions()
                )
                tool_use_context = ToolUseContext(
                    tools=available_tools,
                    dangerous_skip_permissions=self.config_manager.should_skip_permissions()
                )
            
            # 生成工具schemas
            tool_schemas = self.tool_manager.generate_openai_function_schemas(
                tools=tool_use_context.tools,
                context=context,
                dangerous_skip_permissions=tool_use_context.dangerous_skip_permissions
            )
            
            # 调用LLM
            assistant_response = await self._get_assistant_response(
                messages=messages,
                system_prompt=full_system_prompt,
                tool_schemas=tool_schemas,
                tool_use_context=tool_use_context
            )
            
            yield assistant_response
            
            # 处理工具调用
            if assistant_response.tool_calls:
                async for tool_result_message in self._handle_tool_calls(
                    assistant_response.tool_calls,
                    tool_use_context,
                    can_use_tool
                ):
                    yield tool_result_message
            
        except Exception as e:
            logger.error(f"Error in query orchestration: {e}")
            yield Message(
                role="assistant",
                content=f"I encountered an error: {str(e)}. Please try again or rephrase your request."
            )
    
    async def _get_assistant_response(
        self,
        messages: List[Message],
        system_prompt: List[str],
        tool_schemas: List[Dict],
        tool_use_context: ToolUseContext
    ) -> Message:
        """获取助手响应"""
        
        # 转换消息格式为LLM API格式
        api_messages = self._normalize_messages_for_api(messages)
        
        # 构建API调用参数
        api_kwargs = {
            "messages": [
                {"role": "system", "content": "\n".join(system_prompt)},
                *api_messages
            ]
        }
        
        # 如果有工具可用，添加functions参数
        if tool_schemas:
            api_kwargs["functions"] = tool_schemas
        
        try:
            # 调用LLM API
            response = await self.llm_client.generate(**api_kwargs)
            
            # 解析响应
            return self._parse_llm_response(response)
            
        except Exception as e:
            logger.error(f"LLM API call failed: {e}")
            raise
    
    async def _handle_tool_calls(
        self,
        tool_calls: List[Dict],
        tool_use_context: ToolUseContext,
        can_use_tool: Optional[Callable[[str, Dict], bool]] = None
    ) -> AsyncGenerator[Message, None]:
        """处理工具调用，参考Claude-Code的runToolUse()"""
        
        for tool_call in tool_calls:
            try:
                tool_name = tool_call.get("name")
                tool_arguments = tool_call.get("arguments", {})
                tool_call_id = tool_call.get("id", "")
                
                # 查找工具
                tool = self.tool_manager.find_tool_by_name(tool_name)
                if not tool:
                    yield Message(
                        role="tool",
                        content=f"Error: No such tool available: {tool_name}",
                        tool_call_id=tool_call_id,
                        name=tool_name
                    )
                    continue
                
                # 权限检查
                if can_use_tool and not can_use_tool(tool_name, tool_arguments):
                    yield Message(
                        role="tool",
                        content=f"Error: Permission denied for tool: {tool_name}",
                        tool_call_id=tool_call_id,
                        name=tool_name
                    )
                    continue
                
                # 检查危险工具权限
                if (self.tool_manager.is_tool_dangerous(tool_name) and 
                    not tool_use_context.dangerous_skip_permissions):
                    yield Message(
                        role="tool",
                        content=f"Error: Dangerous tool {tool_name} requires permission",
                        tool_call_id=tool_call_id,
                        name=tool_name
                    )
                    continue
                
                # 执行工具
                tool_result = await self._execute_tool(tool, tool_arguments)
                
                yield Message(
                    role="tool",
                    content=tool_result,
                    tool_call_id=tool_call_id,
                    name=tool_name
                )
                
            except Exception as e:
                logger.error(f"Error executing tool {tool_name}: {e}")
                yield Message(
                    role="tool",
                    content=f"Error: Tool execution failed: {str(e)}",
                    tool_call_id=tool_call.get("id", ""),
                    name=tool_call.get("name", "unknown")
                )
    
    async def _execute_tool(self, tool: AgenticBaseTool, arguments: Dict[str, Any]) -> str:
        """执行单个工具"""
        try:
            # 检查工具是否有execute方法
            if hasattr(tool, 'execute') and callable(tool.execute):
                result = tool.execute(**arguments)
                
                # 处理异步结果
                if asyncio.iscoroutine(result):
                    result = await result
                
                # 格式化结果
                if hasattr(result, 'success') and hasattr(result, 'content'):
                    # ToolResult对象
                    if result.success:
                        return str(result.content)
                    else:
                        return f"Error: {result.content}"
                else:
                    return str(result)
            else:
                return f"Error: Tool {tool.__class__.__name__} does not have execute method"
                
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return f"Error: {str(e)}"
    
    def _normalize_messages_for_api(self, messages: List[Message]) -> List[Dict[str, Any]]:
        """将消息标准化为API格式，参考Claude-Code的normalizeMessagesForAPI()"""
        api_messages = []
        
        for message in messages:
            api_message = {
                "role": message.role,
                "content": message.content
            }
            
            # 添加工具调用信息
            if message.tool_calls:
                api_message["tool_calls"] = message.tool_calls
            
            if message.tool_call_id:
                api_message["tool_call_id"] = message.tool_call_id
            
            if message.name:
                api_message["name"] = message.name
            
            api_messages.append(api_message)
        
        return api_messages
    
    def _parse_llm_response(self, response: str) -> Message:
        """解析LLM响应为消息对象"""
        
        # 尝试解析JSON格式的工具调用
        tool_calls = self._extract_tool_calls_from_response(response)
        
        return Message(
            role="assistant",
            content=response,
            tool_calls=tool_calls if tool_calls else None
        )
    
    def _extract_tool_calls_from_response(self, response: str) -> Optional[List[Dict]]:
        """从响应中提取工具调用"""
        try:
            # 查找Action JSON块
            import re
            
            # 匹配 Action: {...} 格式
            action_pattern = r'Action:\s*\{[^}]*\}'
            action_matches = re.findall(action_pattern, response, re.DOTALL)
            
            tool_calls = []
            for i, action_match in enumerate(action_matches):
                # 提取JSON部分
                json_start = action_match.find('{')
                if json_start >= 0:
                    json_str = action_match[json_start:]
                    try:
                        action_data = json.loads(json_str)
                        if "name" in action_data:
                            tool_call = {
                                "id": f"call_{i}",
                                "name": action_data["name"],
                                "arguments": action_data.get("parameters", {})
                            }
                            tool_calls.append(tool_call)
                    except json.JSONDecodeError:
                        continue
            
            return tool_calls if tool_calls else None
            
        except Exception as e:
            logger.warning(f"Error extracting tool calls: {e}")
            return None


# 工厂函数
def create_query_orchestrator(
    llm_client: LLMAPIClient,
    working_dir: Optional[str] = None
) -> QueryOrchestrator:
    """创建查询编排器实例"""
    
    # 导入管理器
    from workers.core.enhanced_tool_manager import get_enhanced_tool_manager
    from workers.core.system_prompts import get_system_prompt_manager
    from workers.core.context_manager import get_context_manager
    from workers.core.config_manager import get_config_manager
    
    tool_manager = get_enhanced_tool_manager()
    system_prompt_manager = get_system_prompt_manager(working_dir=working_dir)
    context_manager = get_context_manager(working_dir=working_dir)
    config_manager = get_config_manager(working_dir=working_dir)
    
    return QueryOrchestrator(
        llm_client=llm_client,
        tool_manager=tool_manager,
        system_prompt_manager=system_prompt_manager,
        context_manager=context_manager,
        config_manager=config_manager
    )



