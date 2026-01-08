"""
Base test utilities for Miaoda tools testing.

This module provides base classes for testing Miaoda tools in real K8S pods,
similar to how they are used in production (miaoda_k8s_example.py).
"""

import sys
import asyncio
import uuid
import os
from pathlib import Path
from typing import Optional, Dict, Any

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from core import K8SToolExecutionNode


class MiaodaToolTestBase:
    """Base class for Miaoda tool tests with K8S pod execution."""

    # K8S configuration (matching miaoda_k8s_example.py)
    NAMESPACE = "rl-training"
    KUBECONFIG_PATH = "./swe-bench-verified-workspace/config_cce_new"
    IMAGE = "iregistry.baidu-int.com/acg-agi/reward:miaoda_reward_1106"

    # Pod configuration
    CPU_REQUEST = "0.3"
    MEMORY_REQUEST = "1Gi"
    TOOL_TIMEOUT = 60.0

    # Environment variables (matching miaoda_k8s_example.py)
    ENVIRONMENT = {
        "PATH": "/usr/local/jupyter:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:/root/.local/share/mise/installs/python/latest/bin:/root/.local/share/mise/installs/node/latest/bin:/pnpm-store",
        "PYTHONPATH": "/workspace",
        "PYTHONIOENCODING": "utf-8",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "http_proxy": os.getenv("HTTP_PROXY", ""),
        "https_proxy": os.getenv("HTTPS_PROXY", ""),
        "PIP_INDEX_URL": os.getenv("PIP_INDEX_URL", "https://pypi.org/simple"),
        "PIP_TRUSTED_HOST": os.getenv("PIP_TRUSTED_HOST", "")
    }

    @staticmethod
    def get_src_dir() -> Path:
        """Get the src directory path."""
        # From src/tools/tests/miaoda, go up to src/
        return Path(__file__).parent.parent.parent.parent

    @staticmethod
    async def create_k8s_executor(test_name: str) -> K8SToolExecutionNode:
        """
        Create a K8S executor for testing.

        Args:
            test_name: Name of the test (used to generate unique pod name)

        Returns:
            K8SToolExecutionNode instance
        """
        # Generate unique pod name
        random_suffix = str(uuid.uuid4())[:8]
        pod_name = f"miaoda-test-{test_name}-{random_suffix}".lower().replace("_", "-")

        # Create K8S executor
        executor = K8SToolExecutionNode(
            name=f"MiaodaTestExecutor-{test_name}",
            namespace=MiaodaToolTestBase.NAMESPACE,
            kubeconfig_path=MiaodaToolTestBase.KUBECONFIG_PATH,
            image=MiaodaToolTestBase.IMAGE,
            pod_name=pod_name,
            environment=MiaodaToolTestBase.ENVIRONMENT,
            cpu_request=MiaodaToolTestBase.CPU_REQUEST,
            memory_request=MiaodaToolTestBase.MEMORY_REQUEST,
            timeline_enabled=False,
            tool_timeout=MiaodaToolTestBase.TOOL_TIMEOUT
        )

        return executor

    @staticmethod
    async def initialize_miaoda_pod(executor: K8SToolExecutionNode, app_id: str = "test-app"):
        """
        Initialize Miaoda pod environment (matching miaoda_k8s_example.py).

        Args:
            executor: K8S executor instance
            app_id: Application ID for workspace directory
        """
        try:
            # Remove old node_modules symlink if exists
            await executor._execute_kubectl_async("rm -f /workspace/node_modules")

            # Create symlink to node_modules
            await executor._execute_kubectl_async("ln -sf /data/shadcn/node_modules /workspace/node_modules")

            # Copy template to workspace
            await executor._execute_kubectl_async(f"cp -r /code-template/react-shadcn-lite-template /workspace/{app_id} || true")

            # Install chardet package
            await executor._execute_kubectl_async("pip install chardet --break-system-packages || true")

        except Exception as e:
            print(f"Warning: Pod initialization encountered error: {e}")
            # Continue even if initialization has issues

    @staticmethod
    async def register_miaoda_tools(executor: K8SToolExecutionNode):
        """
        Register all Miaoda tools on the executor.

        Args:
            executor: K8S executor instance
        """
        # Register all Miaoda tools
        executor.register_tool("miaoda_bash_executor", "src/tools/miaoda/bash_func.py")
        executor.register_tool("miaoda_file_editor", "src/tools/miaoda/file_editor.py")
        executor.register_tool("miaoda_finish", "src/tools/miaoda/finish.py")
        executor.register_tool("miaoda_think", "src/tools/miaoda/think.py")
        executor.register_tool("miaoda_image_search", "src/tools/miaoda/image_search.py")
        executor.register_tool("miaoda_api_rag", "src/tools/miaoda/api_rag.py")
        executor.register_tool("miaoda_api_desc", "src/tools/miaoda/api_desc.py")
        executor.register_tool("miaoda_supabase_init", "src/tools/miaoda/supabase_init.py")
        executor.register_tool("miaoda_supabase_migration", "src/tools/miaoda/supabase_migration.py")
        executor.register_tool("miaoda_supabase_sql", "src/tools/miaoda/supabase_sql_execution.py")

    @staticmethod
    async def execute_tool_in_k8s(
        tool_name: str,
        parameters: Dict[str, Any],
        test_name: str,
        app_id: str = "test-app"
    ) -> Dict[str, Any]:
        """
        Execute a Miaoda tool in a real K8S pod.

        Args:
            tool_name: Name of the tool to execute (e.g., "miaoda_think")
            parameters: Tool parameters
            test_name: Name of the test (for pod naming)
            app_id: Application ID for workspace

        Returns:
            Tool execution result dictionary
        """
        executor = None
        try:
            # Create K8S executor
            executor = await MiaodaToolTestBase.create_k8s_executor(test_name)

            # Use async context manager for automatic cleanup
            async with executor:
                # Initialize pod environment
                await MiaodaToolTestBase.initialize_miaoda_pod(executor, app_id)

                # Register all Miaoda tools
                await MiaodaToolTestBase.register_miaoda_tools(executor)

                # Execute the tool
                tool_call = {
                    "tool": tool_name,
                    "parameters": parameters
                }

                results = await executor.process_async([tool_call])

                if results and len(results) > 0:
                    return results[0]
                else:
                    return {
                        "status": "error",
                        "error": "No result returned from tool execution"
                    }
        finally:
            # Comprehensive cleanup
            if executor:
                try:
                    # Close async manager if it exists
                    if hasattr(executor, 'async_manager') and executor.async_manager:
                        await executor.async_manager.close()

                    # Give time for cleanup
                    await asyncio.sleep(0.1)
                except Exception:
                    # Ignore cleanup errors
                    pass

    @staticmethod
    def assert_success(result: Dict[str, Any], test_name: str):
        """
        Assert that a tool execution succeeded.

        Args:
            result: Tool execution result
            test_name: Name of the test
        """
        if result.get("status") != "success":
            print(f"❌ {test_name}: FAILED")
            print(f"   Status: {result.get('status')}")
            if "error" in result:
                print(f"   Error: {result.get('error', '')[:200]}")
            if "result" in result:
                print(f"   Result: {result.get('result', '')[:200]}")
            raise AssertionError(f"Test {test_name} failed: {result.get('error', 'Unknown error')}")

        print(f"✅ {test_name}: PASSED")
        if "result" in result:
            result_str = str(result.get('result', ''))
            if len(result_str) > 100:
                print(f"   Result: {result_str[:100]}...")
            else:
                print(f"   Result: {result_str}")

    @staticmethod
    def assert_failure(result: Dict[str, Any], test_name: str):
        """
        Assert that a tool execution correctly failed (for negative testing).

        Args:
            result: Tool execution result
            test_name: Name of the test
        """
        if result.get("status") == "success":
            print(f"❌ {test_name}: Should have failed but succeeded")
            raise AssertionError(f"Test {test_name} should have failed")

        print(f"✅ {test_name}: PASSED (correctly failed)")
        if "error" in result:
            error_str = str(result.get('error', ''))
            if len(error_str) > 100:
                print(f"   Error: {error_str[:100]}...")
            else:
                print(f"   Error: {error_str}")
