#!/usr/bin/env python3
"""
Test GeneralAgent with R2E tools for SWE-bench-verified processing using subprocess for true concurrency.
This version uses subprocess to run each instance in a separate process, avoiding thread blocking issues.

"""
from typing import Any

from swebench.harness.constants import (
    APPLY_PATCH_FAIL,
    END_TEST_OUTPUT,
    FAIL_ONLY_REPOS,
    FAIL_TO_FAIL,
    FAIL_TO_PASS,
    KEY_INSTANCE_ID,
    KEY_PREDICTION,
    MAP_REPO_VERSION_TO_SPECS,
    PASS_TO_FAIL,
    PASS_TO_PASS,
    RESET_FAILED,
    START_TEST_OUTPUT,
    TESTS_ERROR,
    TESTS_TIMEOUT,
    EvalType,
    ResolvedStatus,
    TestStatus,
)
from swebench.harness.test_spec.test_spec import TestSpec
from swebench.harness.log_parsers import MAP_REPO_TO_PARSER


# MARK: Utility functions
def test_passed(case: str, sm: dict[str, str]) -> bool:
    return case in sm and sm[case] in [TestStatus.PASSED.value, TestStatus.XFAIL.value]


def test_failed(case: str, sm: dict[str, str]) -> bool:
    return case not in sm or sm[case] in [TestStatus.FAILED.value, TestStatus.ERROR.value]


# MARK: Evaluation report functions
def get_logs_eval(test_spec: TestSpec, log_fp: str) -> tuple[dict[str, str], bool]:
    """
    Retrieve evaluation results for a task instance from its corresponding log file

    Args:
        log_fp (str): path to log file
    Returns:
        bool: whether the patch applied successfully
        dict: status map

    TODO(john-b-yang): Check this is working properly...
    """
    repo = test_spec.repo
    version = test_spec.version
    log_parser = MAP_REPO_TO_PARSER[repo]
    test_cmd = MAP_REPO_VERSION_TO_SPECS[repo][version]["test_cmd"]
    if isinstance(test_cmd, list):
        test_cmd = test_cmd[-1]

    with open(log_fp) as f:
        content = f.read()
        # TODO fix constant here
        bad_codes = list(
            filter(
                lambda x: x in content,
                [
                    APPLY_PATCH_FAIL,
                    RESET_FAILED,
                    TESTS_ERROR,
                    TESTS_TIMEOUT,
                ],
            )
        )
        if bad_codes:
            return {}, False
        elif not (START_TEST_OUTPUT in content and END_TEST_OUTPUT in content):
            # Test patch did not apply (should not happen at all)
            return {}, False

        # Get status map of evaluation results
        content = content.split(START_TEST_OUTPUT)[1].split(END_TEST_OUTPUT)[0]
        return log_parser(content, test_spec), True


def get_eval_tests_report(
    eval_status_map: dict[str, str],
    gold_results: dict[str, str],
    calculate_to_fail: bool = False,
    eval_type: EvalType = EvalType.PASS_AND_FAIL,
) -> dict[str, dict[str, list[str]]]:
    """
    Create a report based on failure/pass change from gold results to eval results.

    Args:
        eval_sm (dict): evaluation status map
        gold_results (dict): gold results
        calculate_to_fail (bool): whether to calculate metrics for "x to fail" tests
    Returns:
        report (dict): report of metrics

    Metric Definitions (Gold Result Pair + Eval Result):
    - Fail-Pass (F2P) + P: Success (Resolution)
    - Pass-Pass (P2P) + P: Success (Maintenance)
    - Fail-Pass (F2P) + F: Failure
    - Pass-Pass (P2P) + F: Failure

    Miscellaneous Definitions
    - Fail-Fail (F2F) + F: Failure Maintenance
    - Pass-Fail (P2F) + F: Not considered
    - Fail-Fail (F2F) + P: Success (Extra Credit)
    - Pass-Fail (P2F) + P: Not considered
    """

    def check_pass_and_fail(test_case, eval_status_map, success, failed):
        if test_passed(test_case, eval_status_map):
            # Assume silent success for now (test case not in eval_sm)
            success.append(test_case)
        elif test_failed(test_case, eval_status_map):
            failed.append(test_case)

    def check_fail_only(test_case, eval_status_map, success, failed):
        if (
            test_case in eval_status_map
            and eval_status_map[test_case] == TestStatus.FAILED.value
        ):
            failed.append(test_case)
        else:
            success.append(test_case)

    check_test_case = (
        check_pass_and_fail if eval_type == EvalType.PASS_AND_FAIL else check_fail_only
    )

    # Calculate resolution metrics
    f2p_success = []
    f2p_failure = []
    for test_case in gold_results[FAIL_TO_PASS]:
        check_test_case(test_case, eval_status_map, f2p_success, f2p_failure)

    # Calculate maintenance metrics
    p2p_success = []
    p2p_failure = []
    for test_case in gold_results[PASS_TO_PASS]:
        check_test_case(test_case, eval_status_map, p2p_success, p2p_failure)

    results = {
        FAIL_TO_PASS: {
            "success": f2p_success,
            "failure": f2p_failure,
        },
        PASS_TO_PASS: {
            "success": p2p_success,
            "failure": p2p_failure,
        },
    }

    f2f_success = []
    f2f_failure = []
    p2f_success = []
    p2f_failure = []
    if calculate_to_fail:
        # Calculate "extra credit" metrics
        for test_case in gold_results[FAIL_TO_FAIL]:
            check_test_case(test_case, eval_status_map, f2f_success, f2f_failure)

        # Calculate not considered metrics
        for test_case in gold_results[PASS_TO_FAIL]:
            check_test_case(test_case, eval_status_map, p2f_success, p2f_failure)

    results.update(
        {
            FAIL_TO_FAIL: {
                "success": f2f_success,
                "failure": f2f_failure,
            },
            PASS_TO_FAIL: {
                "success": p2f_success,
                "failure": p2f_failure,
            },
        }
    )
    return results


def compute_fail_to_pass(report: dict[str, dict[str, Any]]) -> float:
    """
    Compute fail-to-pass metric. Accepts single report as argument.
    """
    total = len(report[FAIL_TO_PASS]["success"]) + len(report[FAIL_TO_PASS]["failure"])
    if total == 0:
        return 1
    return len(report[FAIL_TO_PASS]["success"]) / total


def compute_pass_to_pass(report: dict[str, dict[str, Any]]) -> float:
    """
    Compute pass-to-pass metric. Accepts single report as argument.
    """
    total = len(report[PASS_TO_PASS]["success"]) + len(report[PASS_TO_PASS]["failure"])
    if total == 0:
        # TODO: Don't factor in p2p metrics
        return 1
    return len(report[PASS_TO_PASS]["success"]) / total


def get_resolution_status(report: dict[str, dict[str, Any]]) -> str:
    """
    Determine resolved status of an evaluation instance

    Criteria:
        - If fail-to-pass (Resolution) = 1 and pass-to-pass (Maintenance) = 1 -> FULL
        - If (fail-to-pass (Resolution) < 1 and > 0) and pass-to-pass (Maintenance) = 1 -> PARTIAL
        - Otherwise -> NO
    """
    f2p = compute_fail_to_pass(report)
    p2p = compute_pass_to_pass(report)

    if f2p == 1 and p2p == 1:
        return ResolvedStatus.FULL.value
    elif f2p < 1 and f2p > 0 and p2p == 1:
        return ResolvedStatus.PARTIAL.value
    else:
        return ResolvedStatus.NO.value


def get_eval_report(instance_id,
    test_spec: TestSpec,
    prediction: dict[str, str],
    test_log_path: str,
    include_tests_status: bool,
) -> dict[str, Any]:
    """
    Generate a report of model evaluation results from a prediction, task instance,
    and evaluation log.

    Args:
        test_spec (dict): test spec containing keys "instance_id", "FAIL_TO_PASS", and "PASS_TO_PASS"
        prediction (dict): prediction containing keys "instance_id", "model_name_or_path", and "model_patch"
        log_path (str): path to evaluation log
        include_tests_status (bool): whether to include the status of each test in the returned report
    Returns:
        report (dict): report of metrics
    """
    report_map = {}

    report_map[instance_id] = {
        "patch_is_None": False,
        "patch_exists": False,
        "patch_successfully_applied": False,
        "resolved": False,
    }
    
    report_map[instance_id]["patch_exists"] = True

    # Get evaluation logs
    eval_status_map, found = get_logs_eval(test_spec, test_log_path)

    if not found:
        return report_map
    report_map[instance_id]["patch_successfully_applied"] = True

    eval_ref = {
        KEY_INSTANCE_ID: test_spec.instance_id,
        FAIL_TO_PASS: test_spec.FAIL_TO_PASS,
        PASS_TO_PASS: test_spec.PASS_TO_PASS,
    }

    eval_type = EvalType.FAIL_ONLY if test_spec.repo in FAIL_ONLY_REPOS \
        else EvalType.PASS_AND_FAIL

    report = get_eval_tests_report(
        eval_status_map, eval_ref, eval_type=eval_type
    )
    if get_resolution_status(report) == ResolvedStatus.FULL.value:
        report_map[instance_id]["resolved"] = True

    if include_tests_status:
        report_map[instance_id]["tests_status"] = report  # type: ignore

    return report_map

import asyncio
import sys
import os
import json
import time
import subprocess
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import tempfile
import pickle
from pathlib import Path
import re
from typing import List, Dict, Optional
# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()  # Load .env file from current directory
except ImportError:
    pass  # python-dotenv not installed, skip

import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)



def extract_py_files(test_paths):

    py_files = set()
                                
    for path in test_paths:
        # 如果包含 :: 分隔符，取第一部分（文件路径）
        # 如果没有 :: 分隔符，整个路径就是文件路径
        if "::" in path:
            file_path = path.split("::")[0]
        else:
            file_path = path
                                    
        # 检查是否是Python文件
        if file_path.endswith('.py'):
            py_files.add(file_path)
                                
        # 转换为列表并返回
    return list(py_files)

def process_single_instance(instance_data_file: str, output_file: str, model_name: str = None, log_file: str = None, base_url: str = None) -> None:
    """
    Process a single instance in a subprocess.
    This function will be called by subprocess.run().
    
    Args:
        instance_data_file: Path to pickled instance data
        output_file: Path to save the patch result
        model_name: Model name to use (with index if load balancing)
        log_file: Path to save the subprocess logs
        base_url: AWS region for Bedrock to use (overrides environment variable)
    """
    import sys
    import os
    import json
    import asyncio
    import logging
    import pickle
    import signal
    import atexit
    
    # Add parent directory to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    
    from workers.agents.general_agent import GeneralAgent, dump_trajectory
    from workers.core import create_tool
    from workers.core.trajectory import TrajectoryStep, StepType
    from workers.core.safe_profiler import SafeProfiler
    from workers.core.profiler_visualizer import ProfilerVisualizer
    
    # Import kodo for pod management
    try:
        from kodo import ContainerRunner
    except ImportError:
        print("ERROR: kodo package not found. Please install it with: pip install kodo")
        sys.exit(1)
    
    # Import R2E configurations
    from workers.tools.r2e_configs import (
        CUSTOM_TOOL_DESCRIPTIONS,
        parse_xml_action_custom,
        CustomDescriptionWrapper,
        generate_custom_system_prompt
    )
    
    # Configure logging
    handlers = []
    log_format = '%(asctime)s - [%(process)d] - %(levelname)s - %(name)s - %(message)s'
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))
    handlers.append(console_handler)
    
    # File handler if log_file is provided
    if log_file:
        file_handler = logging.FileHandler(log_file, mode='w')
        file_handler.setFormatter(logging.Formatter(log_format))
        handlers.append(file_handler)
    
    # Configure root logger to capture all logs
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    
    # Ensure R2E tools loggers propagate to root logger
    # Allow debug level to be controlled via environment variable
    r2e_log_level = os.environ.get('R2E_LOG_LEVEL', 'DEBUG')
    if r2e_log_level.upper() == 'DEBUG':
        logging.getLogger('workers.tools.r2e_tools').setLevel(logging.DEBUG)
    else:
        logging.getLogger('workers.tools.r2e_tools').setLevel(logging.INFO)
    
    # Also ensure kodo and other libraries log properly
    logging.getLogger('kodo').setLevel(logging.INFO)
    logging.getLogger('workers').setLevel(logging.INFO)
    
    logger = logging.getLogger(__name__)
    '''

    def convert_log_to_validation_path(log_path):
        """
        将日志文件路径转换为对应的验证文件路径
        
        参数:
            log_path: 日志文件的完整路径，如/mnt/cfs_bj_mt/xuruijie/rollout/0828/logs/astropy__astropy-7166.log
            
        返回:
            对应的验证文件路径，如/mnt/cfs_bj_mt/xuruijie/rollout/0828/validations/astropy__astropy-7166.json
        """
        # 分离目录和文件名
        dir_name, file_name = os.path.split(log_path)
        
        # 替换目录中的logs为validations
        new_dir = dir_name.replace('logs', 'validations')
        
        # 替换文件名的扩展名.log为.json
        new_file_name = os.path.splitext(file_name)[0] + '.json'
        
        # 组合新的路径
        validation_path = os.path.join(new_dir, new_file_name)
        
        return validation_path
    eval_path = convert_log_to_validation_path(log_file)
    if os.path.exists(eval_path):
        return None
    '''
    if log_file:
        logger.info(f"Logging to file: {log_file}")
        # Log active logger configuration
        logger.info(f"Logger configuration:")
        logger.info(f"  Root logger level: {logging.getLogger().level}")
        logger.info(f"  R2E tools logger level: {logging.getLogger('workers.tools.r2e_tools').level}")
        logger.info(f"  Workers logger level: {logging.getLogger('workers').level}")
        logger.info(f"  R2E_LOG_LEVEL env var: {r2e_log_level}")
        # Test that R2E tools logging is working
        test_logger = logging.getLogger('workers.tools.r2e_tools.test')
        test_logger.info("R2E tools logging test - this should appear in the log file")
    
    # Load instance data
    with open(instance_data_file, 'rb') as f:
        data = pickle.load(f)
    
    instance = data['instance']
    namespace = data['namespace']
    kubeconfig_path = data['kubeconfig_path']
    output_dir = data['output_dir']
    enable_profiling = data['enable_profiling']
    max_tokens = data.get('max_tokens', 16000)  # Get max_tokens from data
    provided_base_url = data.get('base_url', None)  # Get base_url from data
    
    instance_id = instance.get("instance_id", "unknown")
    issue = instance.get("problem_statement", "")
    image = instance.get("image", "")
    base_commit = instance.get("base_commit", None)  # Get base_commit from instance data
    
    # Use provided model name or default
    actual_model_name = model_name or MODEL_NAME


    # Use provided base_url or environment variable or default (for backward compatibility)
    actual_base_url = base_url or provided_base_url or "us-west-2"
    
    logger.info(f"Process {os.getpid()}: Processing instance {instance_id} with model {actual_model_name} and region {actual_base_url}")
    
    # Global variables for cleanup
    global_kodo_runner = None
    global_pod = None
    global_pod_name = None
    
    def cleanup_pod():
        """Cleanup function to ensure pod is stopped."""
        global global_kodo_runner, global_pod, global_pod_name
        if global_pod and global_kodo_runner:
            try:
                logger.warning(f"Cleaning up pod {global_pod_name} due to process termination")
                global_kodo_runner.stop_container(global_pod)
                logger.info(f"Pod {global_pod_name} stopped during cleanup")
            except Exception as e:
                logger.error(f"Error stopping pod during cleanup: {e}")
    
    # Register cleanup handlers
    atexit.register(cleanup_pod)
    
    def signal_handler(signum, frame):
        """Handle termination signals."""
        logger.warning(f"Received signal {signum}, cleaning up...")
        cleanup_pod()
        sys.exit(1)
    
    # Register signal handlers for common termination signals
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    async def run_instance():
        """Async function to process the instance."""
        global global_kodo_runner, global_pod, global_pod_name
        
        # Initialize kodo
        kodo_runner = ContainerRunner(
            backend="kubernetes",
            namespace=namespace,
            kubeconfig_path=kubeconfig_path
        )
        global_kodo_runner = kodo_runner
        
        # Create pod name with unique identifier to avoid conflicts
        try:
            import uuid
            unique_suffix = str(uuid.uuid4())[:8]  # Use first 8 chars of UUID
        except ImportError:
            # Fallback to timestamp + process ID if uuid not available
            import time
            unique_suffix = f"{int(time.time()) % 1000000:06d}-{os.getpid() % 100:02d}"
        
        # Keep pod name reasonable length (max 63 chars for K8s)
        instance_part = instance_id.lower().replace('/', '-').replace('_', '-')[:35]
        pod_name = f"swe-{instance_part}-{unique_suffix}"
        global_pod_name = pod_name
        pod = None
        
        try:
            # Start pod with retry logic
            logger.info(f"Starting pod: {pod_name} (unique suffix: {unique_suffix})")
            logger.info(f"Using image: {image}")
            
            max_retries = 3
            retry_delay = 5
            pod = None
            
            for attempt in range(max_retries):
                try:
                    pod = global_pod = kodo_runner.start_container(
                        image,
                        name=pod_name,
                        environment={
                            "PYTHONPATH": "/testbed",
                            "SWE_INSTANCE_ID": instance_id,
                            # UTF-8 encoding settings for LLM-generated code
                            # This prevents UnicodeEncodeError when LLM generates Unicode characters
                            # like checkmarks (âœ“), crosses (âœ—), or other special symbols
                            "PYTHONIOENCODING": "utf-8",
                            "LANG": "C.UTF-8",
                            "LC_ALL": "C.UTF-8",
                            # Proxy settings
                            "http_proxy": "http://agent.baidu.com:8891",
                            "https_proxy": "http://agent.baidu.com:8891",
                            "PIP_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple"
                        }
                    )

                    logger.info(f"Pod {pod_name} started successfully on attempt {attempt + 1}")
                    break
                except Exception as pod_error:
                    error_msg = str(pod_error)
                    logger.error(f"Attempt {attempt + 1}/{max_retries} failed to start pod {pod_name}: {error_msg}")
                    
                    # Check for specific errors
                    if "Response ended prematurely" in error_msg or "ProtocolError" in error_msg:
                        logger.warning("Network/Protocol error detected, will retry...")
                    elif "already exists" in error_msg:
                        # This should be rare now with unique suffixes
                        logger.warning(f"Pod {pod_name} already exists (unexpected with UUID), attempting cleanup...")
                        try:
                            kodo_runner.stop_container(pod_name)
                            await asyncio.sleep(2)
                        except:
                            pass
                    
                    if attempt < max_retries - 1:
                        logger.info(f"Retrying in {retry_delay} seconds...")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        logger.error(f"Failed to start pod after {max_retries} attempts")
                        logger.error(f"Image: {image}")
                        logger.error(f"Namespace: {namespace}")
                        raise
            
            # Wait for pod to be ready
            logger.info(f"Waiting for pod {pod_name} to be ready...")
            await asyncio.sleep(3)
            logger.info(f"Pod {pod_name} should be ready now")

            kodo_runner.execute_command(pod, f"ln -s /opt/miniconda3/envs/testbed /root/.venv")
            kodo_runner.execute_command(pod, f"export https_proxy=http://agent.baidu.com:8891")
            kodo_runner.execute_command(pod, f"export http_proxy=http://agent.baidu.com:8891")
            #install_result = kodo_runner.execute_command(pod, f"python -m pip install -e .")
            #if install_result[1] == 0:
            #        logger.info("✅ pytest installed successfully")
            #else:
            #        logger.warning(f"⚠️ pytest installation may have failed, exit code: {install_result[1]}")
            #        logger.warning(f"Installation output: {install_result[0]}")

            # Setup environment
            '''
            n2,n1 = kodo_runner.execute_command(pod, f"source /opt/miniconda3/bin/activate")
            n4,n3 = kodo_runner.execute_command(pod, f"conda activate testbed")
            n6,n5 = kodo_runner.execute_command(pod, f"python3 -m pip install -e .")
            if n1 == 0:
                logger.info("✅  successfully source /opt/miniconda3/bin/activate")
            else:
                logger.info(n2)
            if n3 == 0:
                logger.info("✅  successfully conda activate testbed")
            else:
                logger.info(n4)
            if n5 == 0:
                logger.info("✅  successfully python3 -m pip install -e .")
            else:
                logger.info(n6)
            '''

            # Install pytest using Tsinghua mirror for faster installation
            '''
            logger.info("Installing pytest using Tsinghua mirror...")
            try:
                _ = kodo_runner.execute_command(pod, "export https_proxy=http://agent.baidu.com:8891")
                _ = kodo_runner.execute_command(pod, "export http_proxy=http://agent.baidu.com:8891")
                install_result = kodo_runner.execute_command(pod, "pip install pytest -i https://pypi.tuna.tsinghua.edu.cn/simple")
                if install_result[1] == 0:
                    logger.info("✅ pytest installed successfully using Tsinghua mirror")
                else:
                    logger.warning(f"⚠️ pytest installation may have failed, exit code: {install_result[1]}")
                    logger.warning(f"Installation output: {install_result[0]}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to install pytest: {e}")
                logger.info("Will continue without pytest installation")
            '''
            
            
            # Create agent with R2E tools
            # R2E assumes working directory is /testbed
            working_dir = "/testbed"
            k8s_config = {
                "execution_mode": "k8s",
                "pod_name": pod_name,
                "namespace": "qianfan-train-cpu-ns",
                "kubeconfig_path": '/mnt/cfs_bj_mt/workspace/tianlun-2/tools/config_cpu',
                "working_dir": working_dir  # Important: R2E tools need to know the working directory
            }
            
            # Create R2E tools with working directory
            base_tools = {
                "r2e_bash_executor": create_tool("R2EBashExecutor", k8s_config.copy()),
                "r2e_file_editor": create_tool("R2EFileEditor", k8s_config.copy()),
                "r2e_search": create_tool("R2ESearch", k8s_config.copy()),
                "r2e_submit": create_tool("R2ESubmit", {})
            }

            '''
            bash_tool = create_tool("R2EBashExecutor", k8s_config.copy())
                        
            result1 = await bash_tool.execute_tool(
                instance_id=f"1",
                parameters={"command": 'source /opt/miniconda3/bin/activate'}
            )
            test_output1 = result1.result.get("stdout", "")
            logger.info(test_output1)

            result2 = await bash_tool.execute_tool(
                instance_id=f"2",
                parameters={"command": "conda activate testbed"}
            )
            test_output2 = result2.result.get("stdout", "")
            logger.info(test_output2)


            result3 = await bash_tool.execute_tool(
                instance_id=f"3",
                parameters={"command": "python3 -m pip install -e ."}
            )
            test_output3 = result3.result.get("stdout", "")
            logger.info(test_output3)
            '''
            
            # Create a wrapper class to log tool executions
            class LoggingToolWrapper:
                def __init__(self, tool, tool_name):
                    self.tool = tool
                    self.tool_name = tool_name
                    self.execution_count = 0
                
                async def execute_tool(self, instance_id, tool_args):
                    self.execution_count += 1
                    import time
                    start_time = time.time()
                    
                    # Log tool invocation
                    logger.info(f"\n{'='*60}")
                    logger.info(f"TOOL EXECUTION #{self.execution_count}: {self.tool_name}")
                    logger.info(f"{'='*60}")
                    logger.info(f"Instance: {instance_id}")
                    logger.info(f"Tool: {self.tool_name}")
                    logger.info(f"Arguments:")
                    for key, value in tool_args.items():
                        if isinstance(value, str) and len(value) > 1000:
                            logger.info(f"  {key}: [String with {len(value)} characters]")
                            logger.info(f"    Preview: {value[:200]}...")
                        else:
                            logger.info(f"  {key}: {value}")
                    
                    try:
                        # Execute the tool
                        result = await self.tool.execute_tool(instance_id, tool_args)
                        execution_time = time.time() - start_time
                        
                        # Log successful execution
                        logger.info(f"\nExecution Result:")
                        logger.info(f"  Status: SUCCESS")
                        logger.info(f"  Execution Time: {execution_time:.2f} seconds")
                        
                        if hasattr(result, 'result'):
                            result_str = str(result.result)
                            if len(result_str) > 2000:
                                logger.info(f"  Output: [Result with {len(result_str)} characters]")
                                logger.info(f"  Output Preview (first 500 chars):")
                                logger.info(f"    {result_str[:500]}...")
                                logger.info(f"  Output Preview (last 500 chars):")
                                logger.info(f"    ...{result_str[-500:]}")
                            else:
                                logger.info(f"  Output: {result_str}")
                        
                        logger.info(f"{'='*60}\n")
                        return result
                        
                    except Exception as e:
                        execution_time = time.time() - start_time
                        
                        # Log error
                        logger.error(f"\nExecution Result:")
                        logger.error(f"  Status: FAILED")
                        logger.error(f"  Execution Time: {execution_time:.2f} seconds")
                        logger.error(f"  Error Type: {type(e).__name__}")
                        logger.error(f"  Error Message: {str(e)}")
                        
                        import traceback
                        logger.error(f"  Traceback:")
                        for line in traceback.format_exc().split('\n'):
                            logger.error(f"    {line}")
                        
                        logger.error(f"{'='*60}\n")
                        raise
                
                def __getattr__(self, name):
                    return getattr(self.tool, name)
            
            # Wrap tools with logging and custom descriptions
            tools = {}
            for tool_name, tool in base_tools.items():
                # First wrap with logging
                logged_tool = LoggingToolWrapper(tool, tool_name)
                
                # Then wrap with custom description if available
                if tool_name in CUSTOM_TOOL_DESCRIPTIONS:
                    tools[tool_name] = CustomDescriptionWrapper(logged_tool, CUSTOM_TOOL_DESCRIPTIONS[tool_name])
                else:
                    tools[tool_name] = logged_tool
            
            # Generate custom system prompt
            custom_system_prompt = generate_custom_system_prompt(
                tools,
                task_description="analyze and fix the reported issue in the repository",
                working_directory="/testbed",
                additional_instructions="\n- Focus on the specific issue described\n- Make minimal changes to fix the issue\n- Ensure your changes don't break existing functionality"
            )
            
            # Create profiler if enabled
            profiler = None
            if enable_profiling:
                profiler = SafeProfiler(enabled=True)
                logger.info(f"Profiling enabled for instance {instance_id}")
            
            # Create agent
            agent = GeneralAgent(
                max_rounds=100,
                debug=True,
                termination_tool_names=["r2e_submit"],
                action_parser=parse_xml_action_custom,
                system_prompt=custom_system_prompt,
                profiler=profiler
            )
            agent.set_tools(tools)
            
            #gold_patch = instance.get("patch", "")
            gold_patch = instance.get("ours_patch", "")
            if not gold_patch:
                return None
            if True:
                # Apply existing patch before test patch if available
                async def apply_existing_patch(
                    instance: Dict[str, Any],
                    k8s_config: Dict[str, Any],
                    pod=None,
                    kodo_runner=None
                ) -> bool:
                    """Apply existing patch from patch_mapping.jsonl if available.
                    
                    Args:
                        instance: Instance data dictionary
                        k8s_config: K8S configuration
                        pod: K8S pod object for executing commands
                        kodo_runner: Kodo runner for pod management
                        
                    Returns:
                        True if patch was applied successfully, False otherwise
                    """
                    instance_id = instance.get("instance_id", "unknown")
                    
                    # Load patch mapping from the specified path
                    #patch_mapping_path = "/mnt/cfs_bj_mt/xuruijie/rollout/claude-sonnet-4-r2e-swebench-0826-v4-mini/patches/patch_mapping.jsonl"
                    
                    try:
                        #with open("/mnt/cfs_bj_mt/xuruijie/rollout/claude-sonnet-4-r2e-swebench-0826-v4-mini/output.json", 'r', encoding='utf-8') as f:
                        #    data = json.load(f)
                        #patch_content = data[instance_id]['model_patch']
                        git_apply_command = f"""cd /testbed && git apply <<'EOF'
{gold_patch}
EOF"""
                        
                        logger.info(f"Applying patch with command: {git_apply_command}")
                        
                        output, exit_code = kodo_runner.execute_command(pod, git_apply_command)
                        
                        if int(exit_code) == 0:
                            logger.info(f"✅ Existing patch applied successfully for instance {instance_id}")
                            return True
                        else:
                            logger.error(f"❌ Failed to apply existing patch for instance {instance_id}: exit_code={output}")
                            logger.error(f"Output: {output}")
                            return False
                            
                    except Exception as e:
                        logger.error(f"❌ Exception while applying existing patch for instance {instance_id}: {e}")
                        return False
                
                # Apply test patch before validation if available
                async def apply_test_patch(
                    instance: Dict[str, Any],
                    k8s_config: Dict[str, Any],
                    pod=None,
                    kodo_runner=None
                ) -> bool:
                    """Apply test patch from the instance data if available.
                    
                    Args:
                        instance: Instance data dictionary
                        k8s_config: K8S configuration
                        pod: K8S pod object for executing commands
                        kodo_runner: Kodo runner for pod management
                        
                    Returns:
                        True if patch was applied successfully, False otherwise
                    """
                    instance_id = instance.get("instance_id", "unknown")
                    test_patch = instance.get("test_patch", "")
                    
                    if not test_patch:
                        logger.info(f"No test patch available for instance {instance_id}")
                        return False
                    
                    logger.info(f"🔧 Applying test patch for instance {instance_id}")
                    
                    try:
                        # Apply the patch using git apply with heredoc syntax directly via kodo_runner
                        git_apply_command = f"""cd /testbed && git apply <<'EOF'
{test_patch}
EOF"""
                        
                        # Create bash executor tool for applying patch
                        #bash_tool = create_tool("R2EBashExecutor", k8s_config.copy())
                        
                        # Execute the command using bash tool
                        #result = await bash_tool.execute_tool(
                        #    instance_id=f"apply_test_patch_{instance_id}",
                        #    parameters={"command": git_apply_command}
                        #)
                        
                        #if True:
                        #    exit_code = result.result.get("return_code", -1)
                        #    if exit_code == 0:
                        #        logger.info(f"✅ Test patch applied successfully for instance {instance_id}")
                        #        return True
                        #    else:
                        #        logger.error(f"❌ Failed to apply test patch for instance {instance_id}")
                        #        logger.error(f"  Exit code: {exit_code}")
                        #        output = result.result.get("stdout", "")
                        #        error = result.result.get("stderr", "")
                        #        logger.error(f"  Output: {output}")
                        #        logger.error(f"  Error: {error}")
                        #        return False
                        #else:
                        #    logger.error(f"❌ Failed to execute test patch command for instance {instance_id}")
                        #    logger.error(f"  Tool error: {result.error}")
                        #    return False

                        logger.info(f"xrj {git_apply_command}")

                        output, exit_code = kodo_runner.execute_command(pod, git_apply_command)
                        
                        if int(exit_code) == 0:
                            logger.info(f"✅ Test patch applied successfully for instance {instance_id}")
                            return True
                        else:
                            logger.error(f"❌ Failed to apply test patch for instance {instance_id}: exit_code={output}")
                            return False

        
                            
                    except Exception as e:
                        logger.error(f"❌ Exception while applying test patch for instance {instance_id}: {e}")
                        return False
                
                # Add unit test validation after patch generation
                async def validate_code_changes(
                    instance: Dict[str, Any], 
                    k8s_config: Dict[str, Any],
                    pod=None,
                    kodo_runner=None, file_name=None
                ) -> Optional[Dict[str, Any]]:
                    """Validate code changes by running tests specified in the instance.
                    
                    Args:
                        instance: Instance data dictionary
                        k8s_config: K8S configuration
                        pod: K8S pod object for executing commands
                        kodo_runner: Kodo runner for pod management
                        
                    Returns:
                        Validation results dictionary or None if no validation data
                    """
                    #if 'http_proxy' in os.environ:
                    #    del os.environ['http_proxy']
                    #if 'https_proxy' in os.environ:
                    #    del os.environ['https_proxy']

                    instance_id = instance.get("instance_id", "unknown")
                    
                    # Apply existing patch before test patch if available
                    existing_patch_applied = await apply_existing_patch(instance, k8s_config, pod, kodo_runner)
                    if existing_patch_applied:
                        logger.info(f"🔧 Existing patch applied successfully, proceeding with test patch")
                    else:
                        logger.info(f"ℹ️ No existing patch applied, proceeding with test patch")
                    
                    # Apply test patch before validation if available
                    #patch_applied = await apply_test_patch(instance, k8s_config, pod, kodo_runner)
                    #if patch_applied:
                    #    logger.info(f"🔧 Test patch applied successfully, proceeding with validation")
                    #else:
                    #    logger.info(f"ℹ️ No test patch applied, proceeding with validation")

                    with open(f'/mnt/cfs_bj_mt/workspace/xuruijie/0824/agentic-rollout-library/swebench_eval/{instance_id}_eval.sh', 'r', encoding='utf-8') as f:
                        #file_content = f.read()
                        lines = f.readlines()       # 按行读取
                        file_content = ''.join(lines)  # 合并成字符串
                    
                    
                    create_file_cmd = f"cat > /testbed/eval.sh << 'EOF'\n{file_content}\nEOF"
                    logger.info(create_file_cmd)
                    output_1, exit_code = kodo_runner.execute_command(pod, create_file_cmd)
                    if int(exit_code) == 0:
                        logging.info('✅ cp eval.sh successfully')
                    else:
                        logging.info('❌ no eval.sh')
                    '''      
                    output_2, exit_code = kodo_runner.execute_command(pod, f"/bin/bash /testbed/eval.sh")
                    if int(exit_code) == 0:
                        logging.info('✅ run eval.sh successfully')
                        logging.info(output_2)
                    else:
                        logging.info('❌ no run eval.sh')
                    '''

                    fail_to_pass_tests = instance.get("FAIL_TO_PASS", [])
                    pass_to_pass_tests = instance.get("PASS_TO_PASS", [])
                    
                    # Handle both string and list formats
                    if isinstance(fail_to_pass_tests, str):
                        try:
                            fail_to_pass_tests = json.loads(fail_to_pass_tests)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse FAIL_TO_PASS tests for {instance_id}: {fail_to_pass_tests}")
                            fail_to_pass_tests = []
                    
                    if isinstance(pass_to_pass_tests, str):
                        try:
                            pass_to_pass_tests = json.loads(pass_to_pass_tests)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse PASS_TO_PASS tests for {instance_id}: {pass_to_pass_tests}")
                            pass_to_pass_tests = []
                    
                    if not fail_to_pass_tests and not pass_to_pass_tests:
                        logger.info(f"No validation tests specified for instance {instance_id}")
                        return None
                    
                    logger.info(f"🔍 Validating code changes for instance {instance_id}")
                    logger.info(f"   FAIL_TO_PASS tests: {len(fail_to_pass_tests)}")
                    logger.info(f"   PASS_TO_PASS tests: {len(pass_to_pass_tests)}")
                    
                    validation_results = {
                        "validation_time": datetime.now().isoformat(),
                        "fail_to_pass_results": {},
                        "pass_to_pass_results": {},
                        "summary": {}
                    }
                    
                    try:
                        # Check if pytest is available before running tests
                        
                        logger.info("Checking pytest availability...")
                        '''
                        try:
                            check_result = kodo_runner.execute_command(pod, "python3 -c 'import pytest; print(f\"pytest version: {pytest.__version__}\")'")
                            if check_result[1] == 0:
                                logger.info(f"✅ pytest is available: {check_result[0].strip()}")
                            else:
                                logger.warning("⚠️ pytest import failed, attempting to install...")
                                install_result = kodo_runner.execute_command(pod, "pip install pytest -i https://pypi.tuna.tsinghua.edu.cn/simple")
                                if install_result[1] == 0:
                                    logger.info("✅ pytest installed successfully using Tsinghua mirror")
                                else:
                                    logger.error(f"❌ Failed to install pytest: {install_result[0]}")
                        except Exception as e:
                            logger.warning(f"⚠️ pytest check failed: {e}")
                        '''
                        

                        if instance_id[:6] == "django":
                            def check_log_for_django(log_file_path):
                                """
                                检查日志文件的最后一行是否有失败
                                
                                Args:
                                    log_file_path (str): 日志文件的路径
                                    
                                Returns:
                                    dict: 包含检查结果的字典
                                        - has_failure (bool): 是否有失败
                                        - last_line (str): 最后一行内容
                                        - total_tests (int): 总测试数
                                        - passed_tests (int): 通过的测试数
                                        - failed_tests (int): 失败的测试数
                                        - skipped_tests (int): 跳过的测试数
                                """
                                import os
                                import re
                             
                                with open(log_file_path, 'r', encoding='utf-8') as f:
                                        log_output = f.read()
                                if "OK" in log_output and "FAILED" not in log_output:

                                    return True
                                else:
                                    return False
                                    
                                    
                                
                             
                            def extract_first_two_fields(test_list):
                                """
                                从测试列表中提取括号内类名的前几个字段（除了最后一个）
                                
                                Args:
                                    test_list: 包含测试名称的列表，格式如 ["test_name (module.class.TestCase)"]
                                
                                Returns:
                                    list: 提取出的字段列表，去重后返回
                                """
                                result = set()  # 使用set去重
                                
                                for test_item in test_list:
                                    # 查找左括号的位置
                                    left_paren = test_item.find('(')
                                    if left_paren != -1:
                                        # 查找右括号的位置
                                        right_paren = test_item.find(')', left_paren)
                                        if right_paren != -1:
                                            # 提取括号内的内容
                                            content_inside = test_item[left_paren + 1:right_paren]
                                            
                                            # 检查括号内是否为空
                                            if not content_inside.strip():
                                                continue  # 如果括号为空，跳过这个元素
                                            
                                            # 按点分割
                                            parts = content_inside.split('.')
                                            
                                            # 如果至少有2个部分，取除了最后一个的所有部分
                                            if len(parts) >= 2:
                                                # 取除了最后一个部分的所有部分
                                                all_except_last = '.'.join(parts[:-1])
                                                result.add(all_except_last)
                                
                                return list(result)
                            
                            

                            #拿到validation_results["fail_to_pass_results"]和
                            logger.info(f"Running Django tests...")
                            #./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1 model_forms.models model_forms.tests validation.models validation.tests
                            #bash_tool = create_tool("R2EBashExecutor", k8s_config.copy())

                            if fail_to_pass_tests:
                                fail_to_pass_results = {}
                                cal_f2p_test = extract_first_two_fields(fail_to_pass_tests)
                                str_f2p_test = ' '.join(cal_f2p_test)

                                            
                                # Run the specific test
                                test_command = f"./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1 {str_f2p_test}"
                                
                                # 在fail_to_pass_tests部分（第692行附近）- 已经正确实现
                                TEST_OUTPUT_START = ">>>>> Start Test Output"
                                TEST_OUTPUT_END = ">>>>> End Test Output"
                                ss = "echo '{TEST_OUTPUT_START}'"
                                create_file_cmd1 = f"echo '{ss}' >> /testbed/eval.sh"
                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd1)
                                test_command = f"./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1 {str_f2p_test}"

                                
                                
                                create_file_cmd = f"echo '{test_command}' >> /testbed/eval.sh"
                                logger.info(test_command)
                                            
                                
                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd)
                                ss = "echo '{TEST_OUTPUT_END}'"
                                create_file_cmd1 = f"echo '{ss}' >> /testbed/eval.sh"
                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd1)
                                output_1, exit_code = kodo_runner.execute_command(pod, '/bin/bash /testbed/eval.sh')

                                validations_dir = os.path.join(output_dir, "validations")
                                os.makedirs(validations_dir, exist_ok=True)
                                
                                # Use same naming convention as trajectories
                                file_safe_instance_id = instance_id.replace('/', '__')
                                validation_file_f2p = os.path.join(validations_dir, f"{file_safe_instance_id}_f2p.log")

                                with open(validation_file_f2p, "w") as f:
                                    f.write(output_1)
                                

                                
                                
                                
                            if pass_to_pass_tests:
                                pass_to_pass_results = {}
                                cal_p2p_test = extract_first_two_fields(pass_to_pass_tests)
                                str_p2p_test = ' '.join(cal_p2p_test)

                                            
                                # Run the specific test

                                test_command = f"./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1 {str_p2p_test}"

                                TEST_OUTPUT_START = ">>>>> Start Test Output"
                                TEST_OUTPUT_END = ">>>>> End Test Output"
                                ss = "echo '{TEST_OUTPUT_START}'"
                                create_file_cmd1 = f"echo '{ss}' >> /testbed/eval.sh"
                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd1)
                                test_command = f"./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1 {str_p2p_test}"
                                
                                logger.info(test_command)


                                create_file_cmd = f"echo '{test_command}' >> /testbed/eval.sh"

                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd)
                                ss = "echo '{TEST_OUTPUT_END}'"
                                create_file_cmd1 = f"echo '{ss}' >> /testbed/eval.sh"
                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd1)
                                output_1, exit_code = kodo_runner.execute_command(pod, '/bin/bash /testbed/eval.sh')

                                validations_dir = os.path.join(output_dir, "validations")
                                os.makedirs(validations_dir, exist_ok=True)
                                
                                # Use same naming convention as trajectories
                                file_safe_instance_id = instance_id.replace('/', '__')
                                validation_file_p2p = os.path.join(validations_dir, f"{file_safe_instance_id}_p2p.log")

                                with open(validation_file_p2p, "w") as f:
                                    f.write(output_1)
                            '''
                            f1 = check_log_for_django(validation_file_f2p) 
                            f2 = check_log_for_django(validation_file_p2p) 

                            results_dir = os.path.join(output_dir, "results")
                            os.makedirs(results_dir, exist_ok=True)
                            results_file = os.path.join(results_dir, f"{file_safe_instance_id}.json")
                            if not f1 and not f2:
                                
                                with open(results_file, 'w', encoding='utf-8') as f:
                                    json.dump({'results': 'both pass'}, f, indent=2, ensure_ascii=False)
                            if f1 and not f2:
                                with open(results_file, 'w', encoding='utf-8') as f:
                                    json.dump({'results': 'f2p fail'}, f, indent=2, ensure_ascii=False)
                            if  not f1 and f2:
                                with open(results_file, 'w', encoding='utf-8') as f:
                                    json.dump({'results': 'f2p fail'}, f, indent=2, ensure_ascii=False)
                            
                            if  not f1 and not f2:
                                with open(results_file, 'w', encoding='utf-8') as f:
                                    json.dump({'results': 'all fail'}, f, indent=2, ensure_ascii=False)
                            '''
                                
                                
                                


                        elif instance_id[:5] == "sympy": 
                            fail_to_pass_results ={}
                            pass_to_pass_results = {}

                            def extract_modified_files_from_patch(patch_content: str) -> List[str]:
                                """
                                从patch内容中提取修改的文件列表
                                
                                Args:
                                    patch_content (str): patch文件的内容
                                    
                                Returns:
                                    List[str]: 修改的文件路径列表
                                """
                                import re
                                
                                # 匹配diff --git行的正则表达式
                                # 格式: diff --git a/path/to/file b/path/to/file
                                git_diff_pattern = r'^diff --git a/(.+?) b/(.+?)$'
                                
                                modified_files = []
                                
                                # 按行分割patch内容
                                lines = patch_content.split('\n')
                                
                                for line in lines:
                                    match = re.match(git_diff_pattern, line)
                                    if match:
                                        # 提取文件路径，通常a和b后面的路径是相同的
                                        file_path = match.group(1)
                                        modified_files.append(file_path)
                                
                                return modified_files
                            test_patch = instance.get("test_patch", "")
                            clean_tests = extract_modified_files_from_patch(test_patch)
                            

                            test_name = " ".join(clean_tests)
                            
                            test_command = f"PYTHONWARNINGS='ignore::UserWarning,ignore::SyntaxWarning' bin/test -C --verbose  {test_name}"

                            TEST_OUTPUT_START = ">>>>> Start Test Output"
                            TEST_OUTPUT_END = ">>>>> End Test Output"
                            ss = "echo '{TEST_OUTPUT_START}'"
                            create_file_cmd1 = f"echo '{ss}' >> /testbed/eval.sh"
                            output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd1)
                            test_command = f"PYTHONWARNINGS='ignore::UserWarning,ignore::SyntaxWarning' bin/test -C --verbose  {test_name}"
                            
                            #拿到validation_results["fail_to_pass_results"]和
                            logger.info(f"Running sympy tests...")


                            create_file_cmd = f"echo '{test_command}' >> /testbed/eval.sh"

                            output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd)

                            ss = "echo '{TEST_OUTPUT_END}'"
                            create_file_cmd1 = f"echo '{ss}' >> /testbed/eval.sh"
                            output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd1)



                            output_1, exit_code = kodo_runner.execute_command(pod, '/bin/bash /testbed/eval.sh')

                            validations_dir = os.path.join(output_dir, "validations")
                            os.makedirs(validations_dir, exist_ok=True)
                                
                            # Use same naming convention as trajectories
                            file_safe_instance_id = instance_id.replace('/', '__')
                            validation_file_f2p = os.path.join(validations_dir, f"{file_safe_instance_id}_f2p.log")
                            validation_file_p2p = os.path.join(validations_dir, f"{file_safe_instance_id}_p2p.log")


                            with open(validation_file_f2p, "w") as f:
                                f.write(output_1)
                            with open(validation_file_p2p, "w") as f:
                                f.write(output_1)
                        
                            
                        else:

                            def check_log_for_failures(log_file_path):
                                """
                                检查日志文件的最后一行是否有失败
                                
                                Args:
                                    log_file_path (str): 日志文件的路径
                                    
                                Returns:
                                    dict: 包含检查结果的字典
                                        - has_failure (bool): 是否有失败
                                        - last_line (str): 最后一行内容
                                        - total_tests (int): 总测试数
                                        - passed_tests (int): 通过的测试数
                                        - failed_tests (int): 失败的测试数
                                        - skipped_tests (int): 跳过的测试数
                                """
                                import os
                                import re
                                
                                result = {
                                    'has_failure': False,
                                    'last_line': '',
                                    'total_tests': 0,
                                    'passed_tests': 0,
                                    'failed_tests': 0,
                                    'skipped_tests': 0
                                }
                                
                                # 检查文件是否存在
                                if not os.path.exists(log_file_path):
                                    print(f"❌ 文件不存在: {log_file_path}")
                                    return result
                                
                                try:
                                    with open(log_file_path, 'r', encoding='utf-8') as f:
                                        lines = f.readlines()
                                        
                                    if not lines:
                                        print(f"⚠️ 文件为空: {log_file_path}")
                                        return result
                                    
                                    # 获取最后一行
                                    last_line = lines[-1].strip()
                                    result['last_line'] = last_line
                                    
                                    # 使用正则表达式匹配测试结果
                                    # 匹配模式: "=================== X failed, Y passed, Z skipped in Ws ===================="
                                    pattern = r'=+\s*(\d+)\s*failed,\s*(\d+)\s*passed,\s*(\d+)\s*skipped\s*in\s*[\d.]+s\s*=+'
                                    match = re.search(pattern, last_line)
                                    
                                    if match:
                                        failed_count = int(match.group(1))
                                        passed_count = int(match.group(2))
                                        skipped_count = int(match.group(3))
                                        
                                        result['failed_tests'] = failed_count
                                        result['passed_tests'] = passed_count
                                        result['skipped_tests'] = skipped_count
                                        result['total_tests'] = failed_count + passed_count + skipped_count
                                        result['has_failure'] = failed_count > 0
                                        
                                        if result['has_failure']:
                                            print(f"❌ 发现失败: {log_file_path}")
                                            print(f"   失败: {failed_count}, 通过: {passed_count}, 跳过: {skipped_count}")
                                        else:
                                            print(f"✅ 全部通过: {log_file_path}")
                                            print(f"   通过: {passed_count}, 跳过: {skipped_count}")
                                    else:
                                        print(f"⚠️ 无法解析最后一行: {last_line}")
                                        
                                except Exception as e:
                                    print(f"❌ 读取文件时出错: {e}")
                                
                                return result
                            
                            def remove_after_bracket(s: str) -> str:
                                return s.split('[', 1)[0]

                            res1 = res2 =  {}
                        
                            # Test FAIL_TO_PASS tests (should pass after fix)
                            if fail_to_pass_tests:
                                logger.info(f"Running FAIL_TO_PASS tests...")
                                fail_to_pass_results = {}

                                clean_fail_to_pass_tests = extract_py_files(fail_to_pass_tests)

                                f2p_parser = " ".join(clean_fail_to_pass_tests)

                                test_command = f"pytest -rA {f2p_parser}"

                                TEST_OUTPUT_START = ">>>>> Start Test Output"
                                TEST_OUTPUT_END = ">>>>> End Test Output"
                                ss = "echo '{TEST_OUTPUT_START}'"
                                create_file_cmd1 = f"echo '{ss}' >> /testbed/eval.sh"
                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd1)
                                test_command = f"pytest -rA {f2p_parser}"

                                create_file_cmd = f"echo '{test_command}' >> /testbed/eval.sh"

                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd)

                                ss = "echo '{TEST_OUTPUT_END}'"
                                create_file_cmd1 = f"echo '{ss}' >> /testbed/eval.sh"
                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd1)
                                output_1, exit_code = kodo_runner.execute_command(pod, '/bin/bash /testbed/eval.sh')

                                validations_dir = os.path.join(output_dir, "validations")
                                os.makedirs(validations_dir, exist_ok=True)
                                
                                # Use same naming convention as trajectories
                                file_safe_instance_id = instance_id.replace('/', '__')
                                validation_file_f2p = os.path.join(validations_dir, f"{file_safe_instance_id}_f2p.log")

                                with open(validation_file_f2p, "w") as f:
                                    f.write(output_1)

                                #res1 = check_log_for_failures(validation_file_f2p)

                                '''
                                
                                for test_name in clean_fail_to_pass_tests:
                                    #   for test_name in fail_to_pass_tests:
                                    try:
                                        # Create bash executor tool for running tests
                                        bash_tool = create_tool("R2EBashExecutor", k8s_config.copy())
                                        
                                        # Run the specific test
                                        test_name = remove_after_bracket(test_name)
                                        
                                        test_command = f"pytest -rA {test_name}"
                                        #test_command = f"python3 -m pytest {test_name}"
                                        logger.info(f"Running test: {test_name}")
                                        
                                        #result = await bash_tool.execute_tool(
                                        #    instance_id=f"validation_{instance_id}",
                                        #    parameters={"command": test_command}
                                        #)
                                        output_1, exit_code = kodo_runner.execute_command(pod, test_command)
                                        
                                        #test_output = result.result.get("stdout", "")
                                        #exit_code = result.result.get("return_code", -1)
                                        logger.info(f"output1: {output_1}")
                                        test_output = output_1
                                        exit_code = int(exit_code)
                                        
                                        if True:
                                            
                                            # Check if test passed (exit code 0)
                                            test_passed = exit_code == 0
                                            
                    
                                            fail_to_pass_results[test_name] = {
                                                    "status": "executed",
                                                    "passed": test_passed,
                                                    "exit_code": exit_code,
                                                    "output": test_output[:1000] + "..." if len(test_output) > 1000 else test_output
                                                }
                                                
                                            logger.info(f"   {test_name}: {'✅ PASSED' if test_passed else '❌ FAILED'}")
                                            
                                        elif  "no tests ran" in test_output.lower():

                                                logger.info(f"   {test_name}: ⏭️ SKIPPED (no tests ran)")
                                                fail_to_pass_results[test_name] = {
                                                        "status": "skipped",
                                                        "reason": "no tests ran",
                                                        "passed": None,
                                                        "exit_code": exit_code,
                                                        "output": test_output[:1000] + "..." if len(test_output) > 1000 else test_output
                                                    }
                                            
                                            
                                        else:
                                            fail_to_pass_results[test_name] = {
                                                "status": "tool_error",
                                                "passed": False,
                                                "exit_code": -1,
                                                "output": f"Tool execution failed: {result.error}"
                                            }
                                            logger.error(f"   {test_name}: ❌ TOOL_ERROR")
                                            
                                    except Exception as e:
                                        fail_to_pass_results[test_name] = {
                                            "status": "exception",
                                            "passed": False,
                                            "exit_code": -1,
                                            "output": f"Exception: {str(e)}"
                                        }
                                        logger.error(f"   {test_name}: ❌ EXCEPTION - {e}")
                                
                                validation_results["fail_to_pass_results"] = fail_to_pass_results
                            
                            # Test PASS_TO_PASS tests (should continue to pass)
                            '''
                            if pass_to_pass_tests:
                                logger.info(f"Running PASS_TO_PASS tests...")
                                pass_to_pass_results = {}

                                clean_pass_to_pass_tests = extract_py_files(pass_to_pass_tests)

                                p2p_parser = " ".join(clean_pass_to_pass_tests)

                                

                                TEST_OUTPUT_START = ">>>>> Start Test Output"
                                TEST_OUTPUT_END = ">>>>> End Test Output"
                                ss = "echo '{TEST_OUTPUT_START}'"
                                create_file_cmd1 = f"echo '{ss}' >> /testbed/eval.sh"
                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd1)
                                test_command = f"pytest -rA {p2p_parser}"

                                create_file_cmd = f"echo '{test_command}' >> /testbed/eval.sh"

                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd)
                                ss = "echo '{TEST_OUTPUT_END}'"
                                create_file_cmd1 = f"echo '{ss}' >> /testbed/eval.sh"
                                output_0, exit_code = kodo_runner.execute_command(pod, create_file_cmd1)
                                output_1, exit_code = kodo_runner.execute_command(pod, '/bin/bash /testbed/eval.sh')

                                validations_dir = os.path.join(output_dir, "validations")
                                os.makedirs(validations_dir, exist_ok=True)
                                
                                # Use same naming convention as trajectories
                                file_safe_instance_id = instance_id.replace('/', '__')
                                validation_file_p2p = os.path.join(validations_dir, f"{file_safe_instance_id}_p2p.log")

                                with open(validation_file_p2p, "w") as f:
                                    f.write(output_1)

                       

                                res2 = check_log_for_failures(validation_file_p2p)
                            '''
                            results_dir = os.path.join(output_dir, "results")
                            os.makedirs(results_dir, exist_ok=True)
                            results_file = os.path.join(results_dir, f"{file_safe_instance_id}.json")
                            if not res1['has_failure'] and not res2['has_failure']:
                                
                                with open(results_file, 'w', encoding='utf-8') as f:
                                    json.dump({'results': 'both pass'}, f, indent=2, ensure_ascii=False)
                            if res1['has_failure'] and not res2['has_failure']:
                                with open(results_file, 'w', encoding='utf-8') as f:
                                    json.dump({'results': 'f2p fail'}, f, indent=2, ensure_ascii=False)
                            if  not res1['has_failure'] and res2['has_failure']:
                                with open(vresults_file, 'w', encoding='utf-8') as f:
                                    json.dump({'results': 'f2p fail'}, f, indent=2, ensure_ascii=False)
                            
                            if  not res1['has_failure'] and not res2['has_failure']:
                                with open(results_file, 'w', encoding='utf-8') as f:
                                    json.dump({'results': 'all fail'}, f, indent=2, ensure_ascii=False)
                            '''
                            
                                
                                

                                

                            '''
                                
                                for test_name in clean_pass_to_pass_tests:
                                    #for test_name in pass_to_pass_tests:
                                    try:
                                        # Create bash executor tool for running tests
                                        #bash_tool = create_tool("R2EBashExecutor", k8s_config.copy())

                                        test_name = remove_after_bracket(test_name)
                                        
                                        # Run the specific test
                                        test_command = f"pytest -rA {test_name}"
                                        #test_command = f"python3 -m pytest {test_name}"
                                        logger.info(f"Running test: {test_name}")
                                        
                                        #result = await bash_tool.execute_tool(
                                        #    instance_id=f"validation_{instance_id}",
                                        #    parameters={"command": test_command}
                                        #)
                                        #test_output = result.result.get("stdout", "")
                                        #exit_code = result.result.get("return_code", -1)

                                        output_2, exit_code = kodo_runner.execute_command(pod, test_command)
                                        
                                        #test_output = result.result.get("stdout", "")
                                        #exit_code = result.result.get("return_code", -1)
                                        logger.info(f"output2: {output_2}")
                                        test_output = output_2
                                        exit_code = int(exit_code)
                                        
                                        if True:
                                            
                                            
                                            # Check if test passed (exit code 0)
                                            test_passed = exit_code == 0

                                            pass_to_pass_results[test_name] = {
                                                    "status": "executed",
                                                    "passed": test_passed,
                                                    "exit_code": exit_code,
                                                    "output": test_output[:1000] + "..." if len(test_output) > 1000 else test_output
                                                }
                                                
                                            logger.info(f"   {test_name}: {'✅ PASSED' if test_passed else '❌ FAILED'}")
                                            
                                            # Check if pytest reported "no tests ran"
                                            
                                        elif "no tests ran" in test_output.lower():
                                                    logger.info(f"   {test_name}: ⏭️ SKIPPED (no tests ran)")
                                                    pass_to_pass_results[test_name] = {
                                                        "status": "skipped",
                                                        "reason": "no tests ran",
                                                        "passed": None,
                                                        "exit_code": exit_code,
                                                        "output": test_output[:1000] + "..." if len(test_output) > 1000 else test_output
                                                    }
                                            
                                        
                                                
                                        else:
                                            pass_to_pass_results[test_name] = {
                                                "status": "tool_error",
                                                "passed": False,
                                                "exit_code": -1,
                                                "output": f"Tool execution failed: {result.error}"
                                            }
                                            logger.error(f"   {test_name}: ❌ TOOL_ERROR")
                                            
                                    except Exception as e:
                                        pass_to_pass_results[test_name] = {
                                            "status": "exception",
                                            "passed": False,
                                            "exit_code": -1,
                                            "output": f"Exception: {str(e)}"
                                        }
                                        logger.error(f"   {test_name}: ❌ EXCEPTION - {e}")
                                
                                validation_results["pass_to_pass_results"] = pass_to_pass_results
                            '''
                            
                            # Calculate summary statistics
                        '''
                        summary = {}
                        
                        if fail_to_pass_tests:
                            # Only count executed tests, skip skipped tests
                            executed_tests = {k: v for k, v in validation_results["fail_to_pass_results"].items() if v["status"] != "skipped"}
                            skipped_tests = {k: v for k, v in validation_results["fail_to_pass_results"].items() if v["status"] == "skipped"}
                            
                            if executed_tests:
                                fail_to_pass_passed = sum(1 for r in executed_tests.values() if r["passed"])
                                fail_to_pass_total = len(executed_tests)
                                summary["fail_to_pass"] = {
                                    "passed": fail_to_pass_passed,
                                    "total": fail_to_pass_total,
                                    "skipped": len(skipped_tests),
                                    "success_rate": fail_to_pass_passed / fail_to_pass_total * 100 if fail_to_pass_total > 0 else 0
                                }
                            else:
                                summary["fail_to_pass"] = {
                                    "passed": 0,
                                    "total": 0,
                                    "skipped": 0,
                                    "success_rate": 0
                                }
                        
                        if pass_to_pass_tests:
                            # Only count executed tests, skip skipped tests
                            executed_tests = {k: v for k, v in validation_results["pass_to_pass_results"].items() if v["status"] != "skipped"}
                            skipped_tests = {k: v for k, v in validation_results["pass_to_pass_results"].items() if v["status"] == "skipped"}
                            
                            if executed_tests:
                                pass_to_pass_passed = sum(1 for r in executed_tests.values() if r["passed"])
                                pass_to_pass_total = len(executed_tests)
                                summary["pass_to_pass"] = {
                                    "passed": pass_to_pass_passed,
                                    "total": pass_to_pass_total,
                                    "skipped": len(skipped_tests),
                                    "success_rate": pass_to_pass_passed / pass_to_pass_total * 100 if pass_to_pass_total > 0 else 0
                                }
                            else:
                                summary["pass_to_pass"] = {
                                    "passed": 0,
                                    "total": 0,
                                    "skipped": 0,
                                    "success_rate": 0
                                }
                        
                        validation_results["summary"] = summary
                        
                        # Log summary
                        logger.info(f"🔍 Validation Summary for {instance_id}:")
                        if fail_to_pass_tests:
                            skipped_count = summary['fail_to_pass'].get('skipped', 0)
                            if skipped_count > 0:
                                logger.info(f"   FAIL_TO_PASS: {summary['fail_to_pass']['passed']}/{summary['fail_to_pass']['total']} ({summary['fail_to_pass']['success_rate']:.1f}%) | ⏭️ Skipped: {skipped_count}")
                            else:
                                logger.info(f"   FAIL_TO_PASS: {summary['fail_to_pass']['passed']}/{summary['fail_to_pass']['total']} ({summary['fail_to_pass']['success_rate']:.1f}%)")
                        if pass_to_pass_tests:
                            skipped_count = summary['pass_to_pass'].get('skipped', 0)
                            if skipped_count > 0:
                                logger.info(f"   PASS_TO_PASS: {summary['pass_to_pass']['passed']}/{summary['pass_to_pass']['total']} ({summary['pass_to_pass']['success_rate']:.1f}%) | ⏭️ Skipped: {skipped_count}")
                            else:
                                logger.info(f"   PASS_TO_PASS: {summary['pass_to_pass']['passed']}/{summary['pass_to_pass']['total']} ({summary['pass_to_pass']['success_rate']:.1f}%)")
                        
                        return validation_results
                        '''
                        
                    except Exception as e:
                        logger.error(f"❌ Validation failed for instance {instance_id}: {e}")
                        error_result = {
                            "validation_time": datetime.now().isoformat(),
                            "error": str(e),
                            "summary": {"error": True}
                        }
                        return error_result
                
                # Run unit tests after LLM processing and patch generation
                logger.info(f"\n{'='*80}")
                logger.info("🧪 Running Unit Tests After LLM Processing")
                logger.info(f"{'='*80}")
                
                validation_results = None
                validation_file = None
                
                try:

                    validation_results = await validate_code_changes(
                        instance=instance,
                        k8s_config=k8s_config,
                        pod=pod,
                        kodo_runner=kodo_runner
                    )
                    
                    if validation_results:
                        logger.info("✅ Unit test validation completed")
                        # Store validation results for later use
                        instance['validation_results'] = validation_results
                        
                        # Save validation results to separate validations folder
                        validations_dir = os.path.join(output_dir, "validations")
                        os.makedirs(validations_dir, exist_ok=True)
                        
                        # Use same naming convention as trajectories
                        file_safe_instance_id = instance_id.replace('/', '__')
                        validation_file = os.path.join(validations_dir, f"{file_safe_instance_id}.json")
                        
                        with open(validation_file, 'w', encoding='utf-8') as f:
                            json.dump(validation_results, f, indent=2, ensure_ascii=False)
                        
                        logger.info(f"Saved validation results to: {validation_file}")
                    else:
                        logger.info("ℹ️ No unit tests to run")
                        
                except Exception as e:
                    logger.error(f"❌ Unit test validation failed: {e}")
                    logger.warning("Continuing despite test validation failure")
                
                logger.info(f"{'='*80}\n")
                
                # Save result for parent process
                result_data = {
                    'success': True,
                    'instance_id': instance_id,
                }
                
                # Add validation file if validation was performed
                if validation_results and validation_file:
                    result_data['validation_file'] = validation_file
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(result_data, f)
                
            
                return None

        except Exception as e:
            # Get detailed error information
            import traceback
            import sys
            
            # Get the full traceback
            exc_type, exc_value, exc_traceback = sys.exc_info()
            tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
            full_traceback = ''.join(tb_lines)
            
            # Log detailed error information
            logger.error(f"Error processing instance {instance_id}: {type(e).__name__}: {e}")
            logger.error(f"Error type: {exc_type}")
            logger.error(f"Error details: {exc_value}")
            logger.error(f"Full traceback:\n{full_traceback}")
            
            # Also print to console for immediate visibility
            print(f"\n{'='*80}")
            print(f"ERROR in instance {instance_id}")
            print(f"Error type: {type(e).__name__}")
            print(f"Error message: {str(e)}")
            print(f"Full traceback:")
            print(full_traceback)
            print(f"{'='*80}\n")
            
            # Try to run validation even if there was an error
            validation_results = None
            validation_file = None
            
            try:
                logger.info(f"\n{'='*80}")
                logger.info("🧪 Attempting Unit Tests After Error")
                logger.info(f"{'='*80}")
                
                validation_results = await validate_code_changes(
                    instance=instance,
                    k8s_config=k8s_config,
                    pod=pod,
                    kodo_runner=kodo_runner
                )
                
                if validation_results:
                    logger.info("✅ Unit test validation completed despite error")
                    # Store validation results for later use
                    instance['validation_results'] = validation_results
                    
                    # Save validation results to separate validations folder
                    validations_dir = os.path.join(output_dir, "validations")
                    os.makedirs(validations_dir, exist_ok=True)
                    
                    # Use same naming convention as trajectories
                    file_safe_instance_id = instance_id.replace('/', '__')
                    validation_file = os.path.join(validations_dir, f"{file_safe_instance_id}.json")
                    
                    with open(validation_file, 'w', encoding='utf-8') as f:
                        json.dump(validation_results, f, indent=2, ensure_ascii=False)
                    
                    logger.info(f"Saved validation results to: {validation_file}")
                else:
                    logger.info("ℹ️ No unit tests to run")
                    
            except Exception as validation_error:
                logger.error(f"❌ Unit test validation also failed: {validation_error}")
            
            logger.info(f"{'='*80}\n")
            
            # Prepare detailed error data
            error_details = {
                'error_type': type(e).__name__,
                'error_message': str(e),
                'traceback': full_traceback,
                'last_checkpoint': 'unknown'
            }
            
            # Try to identify where the error occurred
            if 'pod started successfully' in full_traceback:
                error_details['last_checkpoint'] = 'pod_startup'
            elif 'Running agent' in full_traceback:
                error_details['last_checkpoint'] = 'agent_execution'
            elif 'Generating patch' in full_traceback:
                error_details['last_checkpoint'] = 'patch_generation'
            
            return None
            
        finally:
            # Clean up pod
            if pod and kodo_runner:
                try:
                    logger.info(f"Stopping pod: {pod_name}")
                    kodo_runner.stop_container(pod)
                    logger.info(f"Pod {pod_name} stopped")
                except Exception as e:
                    logger.error(f"Error stopping pod: {e}")
    
    # Run the async function
    asyncio.run(run_instance())


class SubprocessSWEBenchRunner:
    """Runner for SWE-bench instances using subprocess for true concurrency."""
    
    def __init__(self, namespace: str = "default", kubeconfig_path: Optional[str] = None, 
                 output_dir: str = "./swe_patches", max_concurrent: int = 1, 
                 enable_profiling: bool = False, model_name: str = None,
                 model_index_range: Optional[Tuple[int, int]] = None,
                 timeout_seconds: int = 600,  # Default 10 minutes timeout
                 max_tokens: int = 16000,  # Maximum tokens for model output
                 base_url: str = None,  # LLM base URL
                 api_key: str = "",
                 local_mode: bool = False):  # Local mode for debugging
        """Initialize the runner.
        
        Args:
            timeout_seconds: Maximum time in seconds for each instance (default: 600 = 10 minutes)
            max_tokens: Maximum tokens for model output (default: 16000)
            base_url: AWS region for Bedrock (optional, overrides environment variable, default: us-west-2)
            local_mode: If True, run instances locally instead of in subprocess
        """
        self.namespace = namespace
        self.kubeconfig_path = kubeconfig_path
        self.output_dir = output_dir
        self.max_concurrent = max_concurrent
        self.enable_profiling = enable_profiling
        self.model_name = model_name or MODEL_NAME
        self.model_index_range = model_index_range
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.base_url = base_url
        self.api_key = api_key
        self.local_mode = local_mode
        self.patches = {}
        
    async def process_instances(self, instances: List[Dict[str, Any]]):
        """Process multiple instances concurrently using subprocess or locally."""
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Create logs directory for subprocess logs
        if not self.local_mode:
            logs_dir = os.path.join(self.output_dir, "logs")
            os.makedirs(logs_dir, exist_ok=True)
            logger.info(f"Subprocess logs will be saved to: {logs_dir}")
        
        # Create validations directory
        validations_dir = os.path.join(self.output_dir, "validations")
        os.makedirs(validations_dir, exist_ok=True)
        logger.info(f"Validation results will be saved to: {validations_dir}")
        
        temp_dir = tempfile.mkdtemp(prefix="swe_bench_")
        
        logger.info(f"Processing {len(instances)} instances with max concurrency: {self.max_concurrent}")
        if self.model_index_range:
            start_idx, end_idx = self.model_index_range
            model_count = end_idx - start_idx + 1
            logger.info(f"Using {model_count} models for load balancing: {self.model_name}-{start_idx} to {self.model_name}-{end_idx}")
        
        # Create subprocess tasks
        tasks = []
        for i, instance in enumerate(instances):
            # Determine model name for this task
            if self.model_index_range:
                start_idx, end_idx = self.model_index_range
                model_range = end_idx - start_idx + 1
                model_index = (i % model_range) + start_idx
                actual_model_name = f"{self.model_name}-{model_index}"
            else:
                actual_model_name = self.model_name
            
            # Prepare data for subprocess
            instance_data = {
                'instance': instance,
                'namespace': self.namespace,
                'kubeconfig_path': self.kubeconfig_path,
                'output_dir': self.output_dir,
                'enable_profiling': self.enable_profiling,
                'max_tokens': self.max_tokens,  # Add max_tokens to instance data
                'base_url': self.base_url,  # Add AWS region to instance data
                'api_key': self.api_key
            }
            
            # Save instance data to temp file
            instance_data_file = os.path.join(temp_dir, f"instance_{i}.pkl")
            with open(instance_data_file, 'wb') as f:
                pickle.dump(instance_data, f)
            
            # Output file for results
            output_file = os.path.join(temp_dir, f"result_{i}.json")
            
            # Create async task for subprocess or local execution
            if self.local_mode:
                # Local mode: run directly without subprocess
                task = self._run_local_instance(i, len(instances), instance_data_file, output_file, actual_model_name, self.base_url)
            else:
                # Subprocess mode: run in separate process with logging
                instance_id = instance.get('instance_id', f'instance_{i}')
                # Use original instance_id format for log file naming (just replace / with __ for filesystem compatibility)
                file_safe_instance_id = instance_id.replace('/', '__')
                log_file = os.path.join(self.output_dir, "logs", f"{file_safe_instance_id}.log")
                task = self._run_subprocess(i, len(instances), instance_data_file, output_file, actual_model_name, log_file, self.base_url)
            tasks.append(task)
        
        # Run tasks with concurrency limit
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def run_with_semaphore(task_func):
            async with semaphore:
                return await task_func
        
        # Execute all tasks
        start_time = time.time()
        results = await asyncio.gather(*[run_with_semaphore(task) for task in tasks], return_exceptions=True)
        elapsed_time = time.time() - start_time
        
        # Process results and collect statistics
        successful = 0
        failed = 0
        timeout_count = 0
        failed_instances = []  # Track failed instance IDs
        timeout_instances = []  # Track timeout instance IDs
        successful_instances = []  # Track successful instance IDs
        profiler_files = []
        timeline_files = []
        validation_files = []
        
        # Global statistics
        global_stats = {
            'total_tool_calls': 0,
            'total_tool_time': 0.0,
            'tool_calls_by_type': {},
            'total_llm_calls': 0,
            'total_llm_time': 0.0,
            'rounds': {'min': float('inf'), 'max': 0, 'total': 0, 'count': 0},
            'trajectory_lengths': {'min': float('inf'), 'max': 0, 'total': 0, 'count': 0},
            'throughput': {'start_time': start_time, 'completed': 0},
            'termination_stats': {
                'submit_called': 0,
                'max_rounds_reached': 0,
                'other': 0
            }
        }
        
        # Process each result and update statistics
        for i, result in enumerate(results):
            instance_id = instances[i].get('instance_id', f'instance_{i}')
            
            # Update progress
            completed = successful + failed
            progress = (completed / len(instances)) * 100 if len(instances) > 0 else 0
            throughput = completed / elapsed_time if elapsed_time > 0 else 0
            
            if completed % 5 == 0 or completed == len(instances):  # Log every 5 completions
                logger.info(f"Progress: {completed}/{len(instances)} ({progress:.1f}%) | "
                          f"Throughput: {throughput:.2f} rollouts/sec")
            
            if isinstance(result, Exception):
                logger.error(f"Task {i} ({instance_id}) failed with exception: {result}")
                failed += 1
                failed_instances.append(instance_id)
            elif result:
                # Extract statistics if available
                if 'stats' in result and result['stats']:
                    stats = result['stats']
                    
                    # Update tool statistics
                    if 'tool_calls' in stats:
                        for tool_call in stats['tool_calls']:
                            global_stats['total_tool_calls'] += 1
                            global_stats['total_tool_time'] += tool_call.get('time', 0)
                            tool_name = tool_call.get('tool', 'unknown')
                            if tool_name not in global_stats['tool_calls_by_type']:
                                global_stats['tool_calls_by_type'][tool_name] = {'count': 0, 'time': 0}
                            global_stats['tool_calls_by_type'][tool_name]['count'] += 1
                            global_stats['tool_calls_by_type'][tool_name]['time'] += tool_call.get('time', 0)
                    
                    # Update LLM statistics
                    if 'llm_calls' in stats:
                        global_stats['total_llm_calls'] += len(stats['llm_calls'])
                        global_stats['total_llm_time'] += sum(call.get('time', 0) for call in stats['llm_calls'])
                    
                    # Update rounds statistics
                    if 'rounds' in stats:
                        rounds = stats['rounds']
                        global_stats['rounds']['min'] = min(global_stats['rounds']['min'], rounds)
                        global_stats['rounds']['max'] = max(global_stats['rounds']['max'], rounds)
                        global_stats['rounds']['total'] += rounds
                        global_stats['rounds']['count'] += 1
                    
                    # Update trajectory length statistics
                    if 'trajectory_length' in stats:
                        length = stats['trajectory_length']
                        global_stats['trajectory_lengths']['min'] = min(global_stats['trajectory_lengths']['min'], length)
                        global_stats['trajectory_lengths']['max'] = max(global_stats['trajectory_lengths']['max'], length)
                        global_stats['trajectory_lengths']['total'] += length
                        global_stats['trajectory_lengths']['count'] += 1
                    
                    # Update termination statistics
                    if 'termination_reason' in stats:
                        reason = stats['termination_reason']
                        if reason in global_stats['termination_stats']:
                            global_stats['termination_stats'][reason] += 1
                
                if result.get('success'):
                    self.patches[result['instance_id']] = result.get('patch', '')
                    successful += 1
                    successful_instances.append(result['instance_id'])
                    global_stats['throughput']['completed'] += 1
                    
                    
                    # Collect validation files
                    if result.get('validation_file'):
                        validation_files.append(result['validation_file'])
                else:
                    failed += 1
                    if result.get('timeout'):
                        timeout_count += 1
                        timeout_instances.append(result.get('instance_id', instance_id))
                    else:
                        failed_instances.append(result.get('instance_id', instance_id))
                        
                    # Collect validation files even for failed runs
                    if result.get('validation_file'):
                        validation_files.append(result['validation_file'])
            else:
                failed += 1
                failed_instances.append(instance_id)
        
        # Calculate final statistics
        avg_tool_time = global_stats['total_tool_time'] / global_stats['total_tool_calls'] if global_stats['total_tool_calls'] > 0 else 0
        avg_llm_time = global_stats['total_llm_time'] / global_stats['total_llm_calls'] if global_stats['total_llm_calls'] > 0 else 0
        avg_rounds = global_stats['rounds']['total'] / global_stats['rounds']['count'] if global_stats['rounds']['count'] > 0 else 0
        avg_trajectory_length = global_stats['trajectory_lengths']['total'] / global_stats['trajectory_lengths']['count'] if global_stats['trajectory_lengths']['count'] > 0 else 0
        overall_throughput = len(instances) / elapsed_time if elapsed_time > 0 else 0
        
        # Print comprehensive summary
        logger.info(f"\n{'='*80}")
        logger.info(f"PROCESSING SUMMARY")
        logger.info(f"{'='*80}")
        
        # Basic metrics
        logger.info(f"\n📊 Basic Metrics:")
        logger.info(f"  Total instances: {len(instances)}")
        logger.info(f"  Successful: {successful} ({successful/len(instances)*100:.1f}%)")
        logger.info(f"  Failed: {failed} ({failed/len(instances)*100:.1f}%)")
        logger.info(f"  Timeout: {timeout_count} ({timeout_count/len(instances)*100:.1f}%)")
        logger.info(f"  Total time: {elapsed_time:.2f} seconds")
        logger.info(f"  Average time per instance: {elapsed_time/len(instances):.2f} seconds")
        
        # Throughput metrics
        logger.info(f"\n⚡ Throughput:")
        logger.info(f"  Overall: {overall_throughput:.3f} rollouts/sec")
        logger.info(f"  Completed rollouts: {global_stats['throughput']['completed']}")
        if self.max_concurrent > 1:
            logger.info(f"  Concurrency: {self.max_concurrent}x parallel")
        
        # Tool usage statistics
        logger.info(f"\n🔧 Tool Usage:")
        logger.info(f"  Total tool calls: {global_stats['total_tool_calls']}")
        if global_stats['total_tool_calls'] > 0:
            logger.info(f"  Average tool execution time: {avg_tool_time:.3f} seconds")
            logger.info(f"  Tool calls by type:")
            for tool_name, tool_stats in global_stats['tool_calls_by_type'].items():
                avg_time = tool_stats['time'] / tool_stats['count'] if tool_stats['count'] > 0 else 0
                logger.info(f"    - {tool_name}: {tool_stats['count']} calls, avg {avg_time:.3f}s")
        
        # LLM usage statistics
        logger.info(f"\n🤖 LLM Usage:")
        logger.info(f"  Total LLM calls: {global_stats['total_llm_calls']}")
        if global_stats['total_llm_calls'] > 0:
            logger.info(f"  Average LLM call time: {avg_llm_time:.3f} seconds")
            logger.info(f"  Average LLM calls per instance: {global_stats['total_llm_calls']/len(instances):.1f}")
        
        # Trajectory statistics
        logger.info(f"\n📈 Trajectory Statistics:")
        if global_stats['rounds']['count'] > 0:
            logger.info(f"  Rounds: min={global_stats['rounds']['min']}, "
                      f"max={global_stats['rounds']['max']}, avg={avg_rounds:.1f}")
        if global_stats['trajectory_lengths']['count'] > 0:
            logger.info(f"  Trajectory length: min={global_stats['trajectory_lengths']['min']}, "
                      f"max={global_stats['trajectory_lengths']['max']}, avg={avg_trajectory_length:.1f}")
        
        # Termination reasons
        logger.info(f"\n🏁 Termination Reasons:")
        term_stats = global_stats['termination_stats']
        total_terminations = sum(term_stats.values())
        if total_terminations > 0:
            logger.info(f"  Normal (r2e_submit called): {term_stats['submit_called']} ({term_stats['submit_called']/total_terminations*100:.1f}%)")
            logger.info(f"  Max rounds reached: {term_stats['max_rounds_reached']} ({term_stats['max_rounds_reached']/total_terminations*100:.1f}%)")
            logger.info(f"  Other: {term_stats['other']} ({term_stats['other']/total_terminations*100:.1f}%)")
            
            if term_stats['max_rounds_reached'] > 0:
                logger.warning(f"  ⚠️ {term_stats['max_rounds_reached']} instances reached max rounds without calling r2e_submit")
                logger.warning(f"  ⚠️ These patches were generated from partial work")
        
        logger.info(f"{'='*80}")
        
        # Save comprehensive summary
        summary_file = os.path.join(self.output_dir, "summary.json")
        summary = {
            "total_instances": len(instances),
            "successful_patches": successful,
            "failed_instances": failed,
            "timeout_instances": timeout_count,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
            "aws_region": self.base_url,
            "local_mode": self.local_mode,
            "successful_instance_ids": successful_instances,
            "failed_instance_ids": failed_instances,
            "timeout_instance_ids": timeout_instances,
            "timestamp": datetime.now().isoformat(),
            "elapsed_time": elapsed_time,
            "max_concurrent": self.max_concurrent,
            "model_name": self.model_name,
            "model_index_range": self.model_index_range,
            "profiling_enabled": self.enable_profiling,
            "validation_enabled": True,
            "validations_dir": os.path.join(self.output_dir, "validations"),
            "statistics": {
                "throughput": {
                    "overall_rollouts_per_sec": overall_throughput,
                    "avg_time_per_instance": elapsed_time/len(instances) if len(instances) > 0 else 0
                },
                "tool_usage": {
                    "total_calls": global_stats['total_tool_calls'],
                    "total_time": global_stats['total_tool_time'],
                    "avg_time": avg_tool_time,
                    "by_type": global_stats['tool_calls_by_type']
                },
                "llm_usage": {
                    "total_calls": global_stats['total_llm_calls'],
                    "total_time": global_stats['total_llm_time'],
                    "avg_time": avg_llm_time,
                    "avg_calls_per_instance": global_stats['total_llm_calls']/len(instances) if len(instances) > 0 else 0
                },
                "trajectory": {
                    "rounds": {
                        "min": global_stats['rounds']['min'] if global_stats['rounds']['count'] > 0 else None,
                        "max": global_stats['rounds']['max'] if global_stats['rounds']['count'] > 0 else None,
                        "avg": avg_rounds
                    },
                    "length": {
                        "min": global_stats['trajectory_lengths']['min'] if global_stats['trajectory_lengths']['count'] > 0 else None,
                        "max": global_stats['trajectory_lengths']['max'] if global_stats['trajectory_lengths']['count'] > 0 else None,
                        "avg": avg_trajectory_length
                    },
                    "termination_reasons": {
                        "submit_called": global_stats['termination_stats']['submit_called'],
                        "max_rounds_reached": global_stats['termination_stats']['max_rounds_reached'],
                        "other": global_stats['termination_stats']['other']
                    }
                },
                "validation": {
                    "total_files": len(validation_files) if 'validation_files' in locals() else 0,
                    "validations_dir": validations_dir if 'validations_dir' in locals() else None,
                    "validation_files": validation_files if 'validation_files' in locals() else [],
                    "validation_summary": {
                        "total_files": len(validation_files) if 'validation_files' in locals() else 0,
                        "file_paths": validation_files if 'validation_files' in locals() else []
                    }
                }
            }
        }
        
        # Add validation info
        validations_dir = os.path.join(self.output_dir, "validations")
        if os.path.exists(validations_dir):
            import glob
            validation_files = glob.glob(os.path.join(validations_dir, "*.json"))
            summary["validation_files"] = validation_files
            summary["validations_dir"] = validations_dir
            summary["validation_count"] = len(validation_files)
            summary["validation_file_paths"] = validation_files
            summary["validation_summary"] = {
                "total_files": len(validation_files),
                "file_paths": validation_files
            }
            summary["validation_details"] = {
                "total_files": len(validation_files),
                "file_paths": validation_files,
                "validations_dir": validations_dir
            }
            
            logger.info(f"Generated {len(validation_files)} validation result files")
        
        # Add validation files to summary
        if 'validation_files' in locals() and validation_files:
            summary["validation_files"] = validation_files
            summary["validation_count"] = len(validation_files)
            summary["validation_file_paths"] = validation_files
            summary["validation_summary"] = {
                "total_files": len(validation_files),
                "file_paths": validation_files
            }
        
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        
        # Cleanup temp directory
        import shutil
        shutil.rmtree(temp_dir)
        
    async def _run_subprocess(self, index: int, total: int, instance_data_file: str, 
                             output_file: str, model_name: str, log_file: str = None, base_url: str = None) -> Optional[Dict]:
        """Run a single instance in a subprocess with timeout."""
        # Load instance data to get instance_id
        with open(instance_data_file, 'rb') as f:
            data = pickle.load(f)
        instance_id = data['instance'].get('instance_id', f'instance_{index}')
        
        logger.info(f"[{index+1}/{total}] Starting subprocess for {instance_id} with model {model_name} (timeout: {self.timeout_seconds}s)")
        if log_file:
            logger.info(f"  Log file: {log_file}")
        
        # Prepare subprocess command
        cmd = [
            sys.executable,
            "-c",
            f"""
import sys
sys.path.insert(0, '{os.path.dirname(os.path.dirname(__file__))}')
from tests.verfy_unitest.generate_logs.py import process_single_instance
process_single_instance('{instance_data_file}', '{output_file}', '{model_name}', '{log_file if log_file else ""}', '{base_url if base_url else ""}')
"""
        ]
        
        # Run subprocess
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            # Wait for completion with timeout
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds
            )
            
            if process.returncode != 0:
                logger.error(f"[{index+1}/{total}] Subprocess for {instance_id} failed with return code {process.returncode}")
                if stderr:
                    stderr_text = stderr.decode()
                    # Show more of stderr for debugging
                    logger.error(f"Stderr output (first 2000 chars):")
                    logger.error(stderr_text[:2000])
                    
                    # Try to extract specific error information
                    if 'Response ended prematurely' in stderr_text:
                        logger.error("Network/API error detected - Response ended prematurely")
                    if 'traceback' in stderr_text.lower():
                        # Find and show the actual error
                        lines = stderr_text.split('\n')
                        for i, line in enumerate(lines):
                            if 'Traceback' in line:
                                # Show the traceback and following lines
                                traceback_end = min(i + 20, len(lines))
                                logger.error("Python traceback found:")
                                for j in range(i, traceback_end):
                                    logger.error(f"  {lines[j]}")
                                break
                
                if stdout:
                    stdout_text = stdout.decode()
                    if stdout_text.strip():
                        logger.info(f"Stdout output (first 1000 chars):")
                        logger.info(stdout_text[:1000])
                
                return {
                    'success': False,
                    'instance_id': instance_id,
                    'error': f'Process failed with return code {process.returncode}',
                    'stderr': stderr.decode()[:2000] if stderr else None
                }
            
        except asyncio.TimeoutError:
            logger.error(f"[{index+1}/{total}] TIMEOUT: Instance {instance_id} exceeded {self.timeout_seconds}s limit")
            
            # Kill the subprocess
            try:
                process.kill()
                await process.wait()  # Wait for process to actually terminate
            except Exception as e:
                logger.error(f"Error killing subprocess for {instance_id}: {e}")
            
            # Return timeout result
            return {
                'success': False,
                'instance_id': instance_id,
                'error': f'Timeout after {self.timeout_seconds} seconds',
                'timeout': True
            }
        
        # Read result
        if os.path.exists(output_file):
            with open(output_file, 'r') as f:
                result = json.load(f)
            logger.info(f"[{index+1}/{total}] Subprocess completed for {instance_id}: {result.get('success', False)}")
            return result
        else:
            logger.error(f"[{index+1}/{total}] No output file generated for {instance_id}")
            return {
                'success': False,
                'instance_id': instance_id,
                'error': 'No output file generated'
            }
    
    async def _run_local_instance(self, index: int, total: int, instance_data_file: str,
                                 output_file: str, model_name: str, base_url: str = None) -> Optional[Dict]:
        """Run a single instance locally (without subprocess) for debugging."""
        # Load instance data to get instance_id
        with open(instance_data_file, 'rb') as f:
            data = pickle.load(f)
        instance_id = data['instance'].get('instance_id', f'instance_{index}')
        
        logger.info(f"[{index+1}/{total}] Starting local execution for {instance_id} with model {model_name}")
        
        try:
            # Run the instance processing directly
            process_single_instance(instance_data_file, output_file, model_name, None, base_url)
            
            # Read result
            if os.path.exists(output_file):
                with open(output_file, 'r') as f:
                    result = json.load(f)
                logger.info(f"[{index+1}/{total}] Local execution completed for {instance_id}: {result.get('success', False)}")
                return result
            else:
                logger.error(f"[{index+1}/{total}] No output file generated for {instance_id}")
                return {
                    'success': False,
                    'instance_id': instance_id,
                    'error': 'No output file generated'
                }
        except Exception as e:
            logger.error(f"[{index+1}/{total}] Local execution failed for {instance_id}: {e}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'instance_id': instance_id,
                'error': str(e)
            }


async def main():
    """Main function to run SWE-bench processing with subprocess."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run R2E agent on SWE-bench instances using subprocess")
    parser.add_argument("jsonl_file", help="Path to JSONL file with SWE-bench instances")
    parser.add_argument("--output-dir", default="./swe_patches", help="Directory to save patches")
    parser.add_argument("--namespace", default=os.getenv("K8S_NAMESPACE", ""),
                       help="Kubernetes namespace")
    parser.add_argument("--kubeconfig", default=os.getenv("KUBECONFIG", ''),
                       help="Path to kubeconfig file")
    parser.add_argument("--max-concurrent", type=int, default=1,
                       help="Maximum number of concurrent subprocesses")
    parser.add_argument("--enable-profiling", action="store_true",
                       help="Enable performance profiling")
    parser.add_argument("--model-name", type=str, default=None,
                       help="Base model name for load balancing")
    parser.add_argument("--model-index-range", type=str, default=None,
                       help="Model index range for load balancing, format: 'start,end'")
    parser.add_argument("--timeout", type=int, default=600,
                       help="Timeout in seconds for each instance (default: 600 = 10 minutes)")
    parser.add_argument("--max-tokens", type=int, default=16000,
                       help="Maximum tokens for model output (default: 16000)")
    parser.add_argument("--base-url", type=str, default=None,
                       help="AWS region for Bedrock (overrides environment variable, default: us-west-2)")
    parser.add_argument("--api-key", type=str, default=None,
                       help="AWS access key (overrides environment variable)")
    parser.add_argument("--local-mode", action="store_true",
                       help="Run in local mode without subprocess for debugging")
    
    args = parser.parse_args()
    
    # Parse model index range
    model_index_range = None
    if args.model_index_range:
        try:
            parts = args.model_index_range.split(',')
            if len(parts) == 2:
                model_index_range = (int(parts[0]), int(parts[1]))
                logger.info(f"Model index range: {model_index_range[0]} to {model_index_range[1]}")
        except ValueError as e:
            logger.error(f"Invalid model index range: {e}")
            sys.exit(1)
    
    # Load instances
    instances = []
    with open(args.jsonl_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                instances.append(json.loads(line))
    
    logger.info(f"Loaded {len(instances)} instances from {args.jsonl_file}")
    
    # Create runner
    runner = SubprocessSWEBenchRunner(
        namespace=args.namespace,
        kubeconfig_path=args.kubeconfig,
        output_dir=args.output_dir,
        max_concurrent=args.max_concurrent,
        enable_profiling=args.enable_profiling,
        model_name=args.model_name,
        model_index_range=model_index_range,
        timeout_seconds=args.timeout,
        max_tokens=args.max_tokens,
        base_url=args.base_url,
        api_key=args.api_key,
        local_mode=args.local_mode
    )
    
    # Display output directory structure
    logger.info(f"\n{'='*60}")
    logger.info(f"OUTPUT DIRECTORY STRUCTURE")
    logger.info(f"{'='*60}")
    logger.info(f"Output directory: {args.output_dir}")
    logger.info(f"  ├── trajectories/     # Agent execution trajectories")
    logger.info(f"  ├── validations/      # Unit test validation results")
    logger.info(f"  ├── patches/          # Generated code patches")
    if args.enable_profiling:
        logger.info(f"  ├── profiles/        # Performance profiling data")
        logger.info(f"  └── logs/           # Subprocess execution logs")
    else:
        logger.info(f"  └── logs/           # Subprocess execution logs")
    logger.info(f"{'='*60}\n")
    
    # Process instances
    await runner.process_instances(instances)


if __name__ == "__main__":

    
    asyncio.run(main())
