#!/usr/bin/env python3
"""
CC Tools版本的General Agent测试脚本
基于test_r2e_general_agent_on_swe_subprocess.py改编，使用CC Tools工具集
测试General Agent使用CC Tools在K8s环境中解决SWE任务的能力
"""

import asyncio
import argparse
import json
import logging
import multiprocessing
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 添加项目根目录到Python路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from workers.agents.general_agent import GeneralAgent
from workers.core.tool_factory import ToolFactory
from workers.utils.llm_client import create_llm_client
from workers.utils.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)

def setup_logging(log_file: Optional[str] = None):
    """设置日志配置"""
    log_format = "%(asctime)s - [%(process)d] - %(levelname)s - %(message)s"
    if log_file:
        logging.basicConfig(
            level=logging.INFO,
            format=log_format,
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
    else:
        logging.basicConfig(level=logging.INFO, format=log_format)


def create_tool(tool_name: str, config: Dict) -> Any:
    """创建工具实例"""
    factory = ToolFactory()
    try:
        return factory.create_tool(tool_name, config)
    except Exception as e:
        logger.error(f"Failed to create tool {tool_name}: {e}")
        raise


class CCToolsTestFramework:
    """CC Tools测试框架"""
    
    CC_TOOLS_MAPPING = {
        # 基础文件操作工具
        "cc_file_read": "workers.tools.cc_tools.file_read_tool.k8s_file_read_tool.K8sFileReadTool",
        "cc_file_write": "workers.tools.cc_tools.file_write_tool.k8s_file_write_tool.K8sFileWriteTool", 
        "cc_file_edit": "workers.tools.cc_tools.file_edit_tool.k8s_file_edit_tool.K8sFileEditTool",
        # 系统操作工具
        "cc_bash": "workers.tools.cc_tools.bash_tool.k8s_bash_tool.K8sBashTool",
        "cc_ls": "workers.tools.cc_tools.ls_tool.k8s_ls_tool.K8sLSTool",
        # 搜索工具 - 新增
        "cc_glob": "workers.tools.cc_tools.glob_tool.k8s_glob_tool.K8sGlobTool",
        "cc_grep": "workers.tools.cc_tools.grep_tool.k8s_grep_tool.K8sGrepTool",
        # 开发工具
        "cc_nb_read": "workers.tools.cc_tools.nb_read_tool.k8s_nb_read_tool.K8sReadNotebookTool",
        "cc_nb_edit": "workers.tools.cc_tools.nb_edit_tool.k8s_nb_edit_tool.K8sNotebookEditCellTool",
        # 内存管理工具
        "cc_memory_read": "workers.tools.cc_tools.memory_read_tool.k8s_memory_read_tool.K8sMemoryReadTool",
        "cc_memory_write": "workers.tools.cc_tools.memory_write_tool.k8s_memory_write_tool.K8sMemoryWriteTool",
        # 高级功能工具
        "cc_agent": "workers.tools.cc_tools.agent_tool.k8s_agent_tool.K8sAgentTool",
        "cc_architect": "workers.tools.cc_tools.architect_tool.k8s_architect_tool.K8sArchitectTool",
        # 特殊工具
        "cc_think": "workers.tools.cc_tools.think_tool.k8s_think_tool.K8sThinkTool",
        "cc_mcp": "workers.tools.cc_tools.mcp_tool.k8s_mcp_tool.K8sMCPTool",
        "cc_sticker": "workers.tools.cc_tools.sticker_request_tool.k8s_sticker_request_tool.K8sStickerRequestTool",
    }
    
    def __init__(self):
        self.factory = ToolFactory()
        self._register_cc_tools()
    
    def _register_cc_tools(self):
        """注册CC Tools到工具工厂"""
        for tool_name, module_path in self.CC_TOOLS_MAPPING.items():
            try:
                module_name, class_name = module_path.rsplit('.', 1)
                self.factory.register_tool_module(tool_name, module_path)
                logger.info(f"✅ Registered CC tool: {tool_name} -> {class_name}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to register CC tool {tool_name}: {e}")
    
    def create_cc_tools(self, k8s_config: Dict) -> Dict[str, Any]:
        """创建CC Tools工具集"""
        tools = {}
        
        # 基础工具集 - 对应R2E的核心功能
        core_tools = {
            "cc_file_read": k8s_config.copy(),
            "cc_file_write": k8s_config.copy(), 
            "cc_file_edit": k8s_config.copy(),
            "cc_bash": k8s_config.copy(),
            "cc_ls": k8s_config.copy(),
        }
        
        # 高级工具集 - CC Tools独有功能
        advanced_tools = {
            "cc_memory_read": k8s_config.copy(),
            "cc_memory_write": k8s_config.copy(),
            "cc_think": k8s_config.copy(),
            "cc_architect": k8s_config.copy(),
        }
        
        # 合并所有工具
        all_tools = {**core_tools, **advanced_tools}
        
        for tool_name, config in all_tools.items():
            try:
                tool_instance = self.factory.create_tool(tool_name, config)
                tools[tool_name] = tool_instance
                logger.info(f"✅ Created CC tool: {tool_name}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to create CC tool {tool_name}: {e}")
        
        return tools


async def run_cc_tools_test(instance: Dict, args: argparse.Namespace, log_file: str) -> bool:
    """运行CC Tools测试实例"""
    instance_id = instance["instance_id"]
    problem_statement = instance["problem_statement"]
    image = instance.get("image", "python:3.9")
    
    logger.info(f"Processing instance {instance_id} with model {args.model}")
    
    try:
        # 导入K8s管理器
        try:
            from kodo import KubernetesManager
        except ImportError:
            logger.error("❌ kodo library is required for CC Tools. Please install it.")
            return False
        
        # 生成唯一的Pod名称
        unique_suffix = uuid.uuid4().hex[:8]
        pod_name = f"cc-test-{instance_id}-{unique_suffix}"
        namespace = "default"
        kubeconfig_path = None
        
        # 初始化K8s管理器
        kodo_runner = KubernetesManager(namespace=namespace)
        
        logger.info(f"Starting pod: {pod_name} (unique suffix: {unique_suffix})")
        logger.info(f"Using image: {image}")
        
        # 启动Pod
        max_retries = 3
        for attempt in range(max_retries):
            try:
                pod = kodo_runner.start_pod(
                    name=pod_name,
                    image=image
                )
                logger.info(f"Pod {pod_name} started successfully on attempt {attempt + 1}")
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed to start pod after {max_retries} attempts: {e}")
                    return False
                logger.warning(f"Attempt {attempt + 1} failed: {e}, retrying...")
                await asyncio.sleep(5)
        
        # 等待Pod就绪
        logger.info(f"Waiting for pod {pod_name} to be ready...")
        await asyncio.sleep(3)
        logger.info(f"Pod {pod_name} should be ready now")
        
        # 环境初始化 - CC Tools的改进环境设置
        logger.info(f"Setting up enhanced environment in pod {pod_name}...")
        kodo_runner.execute_command(pod, f"ln -s /opt/miniconda3/envs/testbed /root/.venv")
        
        # 🔥 创建多个工作目录以支持CC Tools的灵活性
        setup_commands = [
            "mkdir -p /app",      # CC Tools默认工作目录
            "mkdir -p /testbed",  # 兼容SWE Bench
            "mkdir -p /memory",   # CC Tools内存目录
            "mkdir -p /tmp/workspace",  # 临时工作空间
            "cd /app && pwd",     # 验证目录创建
            "cd /app && git init",  # 初始化Git仓库
        ]
        
        # 🔥 临时跳过环境设置，使用kubectl直接设置
        logger.info("🔧 Using kubectl to setup environment (kodo has ApiException issue)...")
        import subprocess
        
        try:
            # 使用kubectl直接设置环境
            setup_cmd = f"kubectl exec {pod_name} -- bash -c 'mkdir -p /app /testbed /memory /tmp/workspace && cd /testbed && git init'"
            result = subprocess.run(setup_cmd, shell=True, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                logger.info("✅ Environment setup successful via kubectl")
            else:
                logger.warning(f"⚠️ Environment setup warning: {result.stderr}")
                
            # 验证设置
            verify_cmd = f"kubectl exec {pod_name} -- ls -la /app /testbed /memory"
            verify_result = subprocess.run(verify_cmd, shell=True, capture_output=True, text=True, timeout=10)
            
            if verify_result.returncode == 0:
                logger.info("✅ Directory verification successful:")
                logger.info(f"  Output: {verify_result.stdout}")
            else:
                logger.warning(f"⚠️ Directory verification failed: {verify_result.stderr}")
                
        except Exception as e:
            logger.error(f"❌ Environment setup failed: {e}")
            # 继续执行，因为可能某些目录已经存在
        
        logger.info("✅ Enhanced environment setup completed")
        
        # 🔥 修复CC Tools配置，解决权限和路径问题
        k8s_config = {
            "execution_mode": "k8s",
            "pod_name": pod_name,
            "namespace": namespace,
            "kubeconfig_path": kubeconfig_path,
            # CC Tools专用配置 - 修复权限问题
            "base_dir": "/testbed",                # 使用实际工作目录
            "original_workdir": "/",               # 🔥 关键修复：允许访问根目录
            "allowed_root": "/",                   # 🔥 关键修复：允许访问所有目录
            "memory_dir": "/memory",               # 内存存储目录
            "timeout": 30,                         # 操作超时
            "timeout_ms": 120000,                  # 毫秒超时
            "allow_dangerous": True,               # 🔥 关键修复：允许git等命令
            "banned_commands": [],                 # 🔥 关键修复：移除命令限制
            "blocked_commands": [],                # 移除所有命令限制
            "allowed_base_dirs": ["/", "/app", "/testbed", "/tmp", "/memory", "/workspace"],
        }
        
        # 创建CC Tools实例
        framework = CCToolsTestFramework()
        base_tools = framework.create_cc_tools(k8s_config)
        
        # 添加Submit工具 (使用R2E的Submit工具)
        try:
            base_tools["r2e_submit"] = create_tool("R2ESubmit", {})
            logger.info("✅ Added R2E Submit tool for compatibility")
        except Exception as e:
            logger.warning(f"⚠️ Failed to add R2E Submit tool: {e}")
        
        # 创建工具包装器用于日志记录
        class LoggingToolWrapper:
            def __init__(self, tool, tool_name):
                self.tool = tool
                self.tool_name = tool_name
                self.execution_count = 0
            
            async def execute_tool(self, instance_id, tool_args):
                self.execution_count += 1
                import time
                start_time = time.time()
                
                # 🔥 详细的工具执行日志（参考R2E格式）
                logger.info(f"\n{'='*80}")
                logger.info(f"🔧 CC TOOL EXECUTION #{self.execution_count}: {self.tool_name}")
                logger.info(f"{'='*80}")
                logger.info(f"Instance ID: {instance_id}")
                logger.info(f"Tool Args: {tool_args}")
                logger.info(f"Tool Type: {type(self.tool).__name__}")
                logger.info(f"Execution started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
                
                try:
                    logger.info(f"⏳ Executing {self.tool_name}...")
                    result = await self.tool.execute_tool(instance_id, tool_args)
                    execution_time = time.time() - start_time
                    
                    logger.info(f"\n✅ Tool Execution Result:")
                    logger.info(f"  Status: {'SUCCESS' if result.success else 'FAILED'}")
                    logger.info(f"  Execution Time: {execution_time:.3f} seconds")
                    
                    if result.success:
                        if hasattr(result, 'content') and result.content:
                            content_str = str(result.content)
                            if len(content_str) > 2000:
                                logger.info(f"  Content Preview: {content_str[:1000]}...{content_str[-1000:]}")
                            else:
                                logger.info(f"  Content: {content_str}")
                        if hasattr(result, 'output') and result.output:
                            output_str = str(result.output)
                            if len(output_str) > 1000:
                                logger.info(f"  Output Preview: {output_str[:500]}...{output_str[-500:]}")
                            else:
                                logger.info(f"  Output: {output_str}")
                    else:
                        if hasattr(result, 'error') and result.error:
                            logger.warning(f"  Error Details: {result.error}")
                        if hasattr(result, 'message') and result.message:
                            logger.warning(f"  Error Message: {result.message}")
                    
                    logger.info(f"{'='*80}\n")
                    return result
                    
                except Exception as e:
                    execution_time = time.time() - start_time
                    logger.error(f"\n❌ Tool Execution FAILED:")
                    logger.error(f"  Status: EXCEPTION")
                    logger.error(f"  Execution Time: {execution_time:.3f} seconds")
                    logger.error(f"  Exception Type: {type(e).__name__}")
                    logger.error(f"  Exception Details: {str(e)}")
                    logger.error(f"{'='*80}\n")
                    raise
            
            def get_openai_tool_schema(self):
                return self.tool.get_openai_tool_schema()
        
        # 包装所有工具
        wrapped_tools = {}
        for tool_name, tool_instance in base_tools.items():
            wrapped_tools[tool_name] = LoggingToolWrapper(tool_instance, tool_name)
        
        # 初始化所有工具实例
        for tool_name, tool_wrapper in wrapped_tools.items():
            try:
                if hasattr(tool_wrapper.tool, '_initialize_instance'):
                    await tool_wrapper.tool._initialize_instance(instance_id)
                logger.info(f"Initialized tool: {tool_name}")
            except Exception as e:
                logger.warning(f"Failed to initialize tool {tool_name}: {e}")
        
        # 创建LLM客户端
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL") 
        model_name = os.getenv("LLM_MODEL_NAME", args.model)
        
        if not api_key or not base_url:
            logger.error("❌ LLM_API_KEY and LLM_BASE_URL environment variables must be set")
            return False
        
        llm_client = create_llm_client(
            api_key=api_key,
            base_url=base_url,
            model=model_name
        )
        
        # 🔥 添加详细的LLM调用日志（参考R2E脚本）
        original_generate = llm_client.generate
        llm_call_count = 0
        
        async def generate_with_logging(messages, **kwargs):
            nonlocal llm_call_count
            llm_call_count += 1
            
            # Use max_tokens from args if provided
            if 'max_tokens' not in kwargs:
                kwargs['max_tokens'] = args.max_tokens
            
            # Log LLM call
            logger.info(f"\n{'~'*60}")
            logger.info(f"CC TOOLS LLM CALL #{llm_call_count}")
            logger.info(f"{'~'*60}")
            logger.info(f"Model: {model_name}")
            logger.info(f"Max Tokens: {kwargs.get('max_tokens', 'default')}")
            logger.info(f"Temperature: {kwargs.get('temperature', 'default')}")
            logger.info(f"Messages: {len(messages)} messages")
            
            # Log all messages for better debugging
            logger.info(f"\nConversation History:")
            for idx, msg in enumerate(messages):
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')
                logger.info(f"\n  Message #{idx+1} - Role: {role}")
                logger.info(f"  Content Length: {len(content)} characters")
                
                if role == 'system':
                    # System message - log first 500 chars
                    if len(content) > 500:
                        logger.info(f"  Content Preview: {content[:500]}...")
                    else:
                        logger.info(f"  Content: {content}")
                elif role in ['user', 'assistant']:
                    # User/Assistant messages - log last part (more relevant)
                    if len(content) > 1000:
                        logger.info(f"  Content Preview: ...{content[-1000:]}")
                    else:
                        logger.info(f"  Content: {content}")
            
            logger.info(f"\n⏳ Sending request to LLM...")
            
            try:
                import time
                start_time = time.time()
                response = await original_generate(messages, **kwargs)
                execution_time = time.time() - start_time
                
                logger.info(f"\n✅ LLM Response Received:")
                logger.info(f"  Status: SUCCESS")
                logger.info(f"  Response Time: {execution_time:.2f} seconds")
                logger.info(f"  Response Length: {len(response)} characters")
                
                # Analyze response content
                if "action" in response.lower() and "name" in response.lower():
                    # Try to extract function calls
                    import re
                    function_calls = re.findall(r'"name":\s*"([^"]+)"', response)
                    if function_calls:
                        logger.info(f"  🔧 LLM Actions: Calling tools: {', '.join(function_calls)}")
                elif 'thought:' in response.lower() or 'reasoning:' in response.lower():
                    logger.info(f"  🤔 LLM Actions: Thinking/Reasoning")
                elif 'answer:' in response.lower() or 'response:' in response.lower():
                    logger.info(f"  📝 LLM Actions: Providing answer")
                
                # Log response content (truncated for readability)
                if len(response) > 2000:
                    logger.info(f"  Response Preview: {response[:1000]}...{response[-1000:]}")
                else:
                    logger.info(f"  Full Response: {response}")
                
                logger.info(f"{'~'*60}\n")
                return response
                
            except Exception as e:
                import time
                execution_time = time.time() - start_time
                logger.error(f"\n❌ LLM Response FAILED:")
                logger.error(f"  Status: FAILED")
                logger.error(f"  Response Time: {execution_time:.2f} seconds")
                logger.error(f"  Error: {e}")
                logger.error(f"{'~'*60}\n")
                raise
        
        llm_client.generate = generate_with_logging
        
        # 创建General Agent (修复配置)
        agent = GeneralAgent(
            max_rounds=args.max_rounds
        )
        
        # 🔥 正确设置工具
        agent.set_tools(wrapped_tools)
        
        logger.info(f"Initialized GeneralAgent with max_rounds={args.max_rounds}")
        logger.info(f"Agent GeneralAgent configured with {len(wrapped_tools)} CC tools")
        
        # 构建提示词
        prompt_builder = PromptBuilder()
        
        # CC Tools的增强系统提示
        system_prompt = """You are a programming agent equipped with advanced CC Tools for development tasks in a Kubernetes environment.

Available CC Tools:
- cc_file_read: Read files with security boundaries
- cc_file_write: Write files to allowed directories  
- cc_file_edit: Edit files with search-replace functionality
- cc_bash: Execute shell commands with working directory support
- cc_ls: List directory contents with tree structure
- cc_memory_read/write: Persistent memory storage
- cc_think: Record thoughts and analysis
- cc_architect: Technical analysis and planning

Working Environment:
- Primary workspace: /app (recommended)
- Compatible workspace: /testbed (for SWE compatibility)  
- Memory storage: /memory
- Temp workspace: /tmp/workspace

Best Practices:
1. Use cc_think to record your analysis and planning
2. Use cc_architect for complex technical decisions
3. Start work in /app directory (CC Tools optimized)
4. Use cc_memory_* for persistent data between operations
5. Leverage cc_bash with working_directory parameter for flexibility

Security: All tools have built-in security boundaries and path validation."""

        user_prompt = f"""
Consider the following software engineering task:
<task>            
{problem_statement}
</task>

Please implement the necessary changes to solve this task. You have access to a complete development environment with CC Tools.

IMPORTANT INSTRUCTIONS:
1. Start by exploring the environment using cc_bash (use 'pwd' and 'ls' commands)
2. Use cc_ls with ABSOLUTE paths only (e.g., cc_ls with path="/testbed")
3. Use cc_glob for file pattern matching (e.g., "**/*.py", "src/**/*.ts")
4. Use cc_grep for content search with regex patterns
5. Use cc_think to plan your approach  
6. Work primarily in /testbed directory (current working directory)
7. Use cc_architect for complex analysis if needed
8. Create files and run tests as appropriate
9. When complete, use r2e_submit to finish

NOTE: cc_ls tool requires absolute paths, NOT relative paths like "." or ".."
TIP: Use cc_glob and cc_grep for efficient code exploration and search

Focus on writing clean, working code that solves the specific task described.
"""

        # 运行Agent
        logger.info("Running agent to solve the issue...")
        logger.info(f"LLM Configuration: model={model_name}, base_url={base_url}, max_tokens={args.max_tokens}")
        
        start_time = time.time()
        
        trajectory = await agent.run_trajectory(
            prompt=user_prompt,
            llm_generate_func=llm_client.generate,
            request_id=instance_id,
            system_prompt=system_prompt,
            max_tokens=args.max_tokens
        )
        
        execution_time = time.time() - start_time
        logger.info(f"Agent execution completed in {execution_time:.2f} seconds")
        
        # 保存轨迹
        trajectory_dir = Path(args.output_dir) / "trajectories"
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        trajectory_file = trajectory_dir / f"{instance_id}_{timestamp}.jsonl"
        
        with open(trajectory_file, 'w', encoding='utf-8') as f:
            if hasattr(trajectory, 'to_jsonl'):
                f.write(trajectory.to_jsonl())
            else:
                # 🔥 修复：正确迭代trajectory.steps而不是trajectory
                for step in trajectory.steps:
                    step_dict = {
                        "step_type": step.step_type.value if step.step_type else None,
                        "content": step.content,
                        "metadata": step.metadata,
                        "tool_name": step.tool_name,
                        "tool_args": step.tool_args,
                        "tool_result": str(step.tool_result) if step.tool_result else None,
                        "reward_score": step.reward_score,
                        "is_correct": step.is_correct,
                    }
                    f.write(json.dumps(step_dict, ensure_ascii=False) + '\n')
        
        logger.info(f"💾 Trajectory saved to: {trajectory_file}")
        
        # 清理工具实例
        for tool_name, tool_wrapper in wrapped_tools.items():
            try:
                if hasattr(tool_wrapper.tool, '_cleanup_instance'):
                    await tool_wrapper.tool._cleanup_instance(instance_id)
            except Exception as e:
                logger.warning(f"Failed to cleanup tool {tool_name}: {e}")
        
        # 检查是否成功完成（查找submit调用）
        success = any("r2e_submit" in str(step) for step in trajectory.steps)
        
        logger.info(f"✅ Instance {instance_id} processing completed. Success: {success}")
        return success
        
    except Exception as e:
        logger.error(f"❌ Error processing instance {instance_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def run_subprocess(instance: Dict, args: argparse.Namespace, log_dir: str) -> bool:
    """在子进程中运行单个测试实例"""
    instance_id = instance["instance_id"]
    log_file = os.path.join(log_dir, f"{instance_id}.log")
    
    # 设置子进程日志
    setup_logging(log_file)
    
    try:
        # 运行异步测试
        result = asyncio.run(run_cc_tools_test(instance, args, log_file))
        return result
    except Exception as e:
        logger.error(f"Subprocess failed for {instance_id}: {e}")
        return False


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="CC Tools General Agent SWE Test")
    parser.add_argument("jsonl_file", help="JSONL file containing test instances")
    parser.add_argument("--model", default="moonshotai/Kimi-K2-Instruct", help="LLM model name")
    parser.add_argument("--max-concurrent", type=int, default=1, help="Maximum concurrent processes")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout per instance (seconds)")
    parser.add_argument("--max-tokens", type=int, default=4000, help="Maximum tokens per LLM call")
    parser.add_argument("--max-rounds", type=int, default=50, help="Maximum agent rounds")
    parser.add_argument("--output-dir", default="./cc_patches", help="Output directory for results")
    
    args = parser.parse_args()
    
    # 设置主进程日志
    log_dir = os.path.join(args.output_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    setup_logging()
    
    # 加载测试实例
    instances = []
    with open(args.jsonl_file, 'r', encoding='utf-8') as f:
        for line in f:
            instances.append(json.loads(line.strip()))
    
    logger.info(f"Loaded {len(instances)} instances from {args.jsonl_file}")
    logger.info(f"Subprocess logs will be saved to: {log_dir}")
    
    # 处理实例
    start_time = time.time()
    successful = 0
    failed = 0
    timeout_count = 0
    
    logger.info(f"Processing {len(instances)} instances with max concurrency: {args.max_concurrent}")
    
    # 使用进程池处理
    with multiprocessing.Pool(args.max_concurrent) as pool:
        results = []
        
        for i, instance in enumerate(instances):
            instance_id = instance["instance_id"]
            logger.info(f"[{i+1}/{len(instances)}] Starting subprocess for {instance_id} with model {args.model} (timeout: {args.timeout}s)")
            
            # 启动子进程
            async_result = pool.apply_async(run_subprocess, (instance, args, log_dir))
            results.append((instance_id, async_result))
            
            logger.info(f"  Log file: {log_dir}/{instance_id}.log")
        
        # 等待所有结果
        for instance_id, async_result in results:
            try:
                success = async_result.get(timeout=args.timeout)
                if success:
                    successful += 1
                    logger.info(f"[{successful+failed+timeout_count}/{len(instances)}] ✅ {instance_id}: SUCCESS")
                else:
                    failed += 1
                    logger.info(f"[{successful+failed+timeout_count}/{len(instances)}] ❌ {instance_id}: FAILED")
            except multiprocessing.TimeoutError:
                timeout_count += 1
                logger.error(f"[{successful+failed+timeout_count}/{len(instances)}] ⏰ TIMEOUT: Instance {instance_id} exceeded {args.timeout}s limit")
            
            # 显示进度
            total_processed = successful + failed + timeout_count
            throughput = total_processed / (time.time() - start_time) if total_processed > 0 else 0
            logger.info(f"Progress: {successful}/{len(instances)} ({successful/len(instances)*100:.1f}%) | Throughput: {throughput:.2f} rollouts/sec")
    
    # 最终统计
    total_time = time.time() - start_time
    logger.info("\n" + "="*80)
    logger.info("CC TOOLS PROCESSING SUMMARY")
    logger.info("="*80)
    logger.info(f"\n📊 Basic Metrics:")
    logger.info(f"  Total instances: {len(instances)}")
    logger.info(f"  Successful: {successful} ({successful/len(instances)*100:.1f}%)")
    logger.info(f"  Failed: {failed} ({failed/len(instances)*100:.1f}%)")
    logger.info(f"  Timeout: {timeout_count} ({timeout_count/len(instances)*100:.1f}%)")
    logger.info(f"  Total time: {total_time:.2f} seconds")
    logger.info(f"  Average time per instance: {total_time/len(instances):.2f} seconds")
    logger.info(f"\n⚡ Throughput:")
    logger.info(f"  Overall: {len(instances)/total_time:.3f} rollouts/sec")
    logger.info(f"  Completed rollouts: {successful}")
    logger.info("="*80)


if __name__ == "__main__":
    asyncio.run(main())
