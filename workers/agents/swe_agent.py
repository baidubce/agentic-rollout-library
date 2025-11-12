#!/usr/bin/env python3
"""
SWE (Software Engineering) Agent implementation.

This agent is specifically designed for software engineering tasks and extends the GeneralAgent
with SWE-specific capabilities including:
- Integration with Kubernetes for containerized execution environments
- Support for bash command execution in isolated containers
- File editing and code manipulation tools
- Search and navigation capabilities for codebases
- Specialized system prompts for software engineering workflows
"""

import json
import logging
import re
import time
import shlex
import asyncio
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime

from ..core.base_agent import BaseAgent
from ..core.registry import register_agent
from ..core.trajectory import Trajectory, TrajectoryStep, StepType
from ..core.profiler import RolloutProfiler, EventType, get_profiler
from .general_agent import GeneralAgent

from kodo import KubernetesManager

logger = logging.getLogger(__name__)

try:
    from json_repair import repair_json
    REPAIR_JSON_AVAILABLE = True
except ImportError:
    REPAIR_JSON_AVAILABLE = False
    logger.warning("json-repair not available. Install with: pip install json-repair")


@register_agent("swe")
class SweAgent(GeneralAgent):
    """
    General purpose ReAct agent with configurable tools and behavior.
    
    This agent supports:
    - Configurable system prompts
    - Configurable tool lists  
    - Configurable maximum rounds/steps
    - Configurable termination tool names
    - Trajectory recording and dumping
    """
    
    def __init__(
        self,
        termination_tool_names: Optional[List[str]] = None,
        debug: bool = False,
        working_dir: str = None,
        namespace: str = None,
        kubeconfig_path: str = None,
        timeout: int = None,
        **kwargs
    ):
        """
        Initialize the General Agent.
        
        Args:
            system_prompt: Custom system prompt to use
            tool_names: List of tool names to register for this agent
            max_rounds: Maximum number of reasoning rounds
            termination_tool_names: List of tool names that signal termination
            debug: Enable debug mode to log all LLM inputs and outputs
            action_parser: Custom function to parse actions from LLM output
            steps_remaining_template: Template for steps remaining message. 
                                     Use {steps} placeholder for step count.
                                     Default: "\n\nSteps Remaining: {steps}"
            show_steps_remaining: Whether to show steps remaining in user messages
            profiler: Optional profiler instance for performance tracking
            **kwargs: Additional configuration passed to BaseAgent
        """
        super().__init__(**kwargs)  # *2 for thought+action pairs
        
        self.termination_tool_names = termination_tool_names or ["finish"]
        self.debug = debug
        self.working_dir = working_dir or "/testbed"
        self.kubeconfig_path = kubeconfig_path
        self.namespace = namespace
        self.timeout = timeout
        self.k8s_manager = None

        self.trajectory = None
        
        logger.info(f"Initialized GeneralAgent with working_dir={working_dir}, kubeconfig_path={kubeconfig_path}, max_rounds={self.max_rounds}")
    
    def _add_steps_remaining(self, messages: List[Dict[str, str]], current_round: int) -> List[Dict[str, str]]:
        """
        Add steps remaining information to the last user message.
        
        Args:
            messages: List of messages
            current_round: Current round number (0-indexed)
            
        Returns:
            Modified messages list with steps remaining added
        """
        if not messages:
            return messages
        
        # Calculate remaining steps
        max_steps = self.max_rounds
        
        # Find the last user message and add steps remaining
        index = 1
        messages_copy = messages.copy()
        for i in range(len(messages_copy)):
            if messages_copy[i].get("role") == "user":
                # Add steps remaining to the last user message
                original_content = messages_copy[i]["content"]
                steps_info = self.steps_remaining_template.format(steps=(max_steps - index))
                messages_copy[i] = {
                    "role": "user",
                    "content": original_content + steps_info
                }
                index += 1
                
        
        return messages_copy

    async def run_trajectory(
        self,
        prompt: Union[str, Dict[str, Any]],
        llm_generate_func,
        request_id: str,
        max_tokens: Optional[int] = None,
        tokenizer_func: Optional[callable] = None,
        chat_parser: Any = None,
        idx: int = None,
        application_id: str = None,
        trajectory_timeout: int = None,
        convert_messages_to_tokens_and_masks: Any = None,
        pod_name: str = None,
        executor: Any = None,
        **kwargs
    ) -> Trajectory:
        """
        Run a complete ReAct trajectory.
        
        Args:
            prompt: Initial prompt or task description
            llm_generate_func: LLM generation function
            request_id: Request ID
            max_tokens: Maximum token limit
            tokenizer_func: Tokenizer function
            chat_parser: Chat parser
            idx: Index
            application_id: Application ID
            trajectory_timeout: Trajectory timeout duration
            convert_messages_to_tokens_and_masks: Message conversion function
            pod_name: Pod name
            executor: Executor
            **kwargs: Additional parameters
            
        Returns:
            Trajectory: Complete trajectory object
        """
        self.pod_name = pod_name
        # Get profiler instance (use provided or global)
        profiler = self.profiler or get_profiler()
        profiler.enabled = False
        
        # Start trajectory profiling
        trajectory_event_id = None
        if profiler.enabled:
            trajectory_event_id = profiler.start_event(
                f"trajectory_{request_id}",
                EventType.TRAJECTORY_STEP,
                {"request_id": request_id}
            )
        
        trajectory = Trajectory(request_id=request_id)
        
        # Initialize trajectory metadata
        # Try to get model name from llm_generate_func if it's an LLMAPIClient method
        model_name = "unknown"
        if hasattr(llm_generate_func, "__self__") and hasattr(llm_generate_func.__self__, "model"):
            model_name = llm_generate_func.__self__.model
        elif "model_name" in kwargs:
            model_name = kwargs["model_name"]
        
        trajectory.metadata = {
            "start_time": datetime.now().isoformat(),
            "model": model_name,
            "max_rounds": self.max_rounds,
            "assistant_timings": [],  # List of timing for each assistant response
            "tool_calls": [],  # List of tool names called
            "message_lengths": [],  # List of message lengths
            "total_tool_calls": 0,
            "error_count": 0,
            "stop_reason": None,  # Will be set to ENV_DONE, MAX_STEPS, or TRUNCATION
            "max_tokens": max_tokens,
            "current_tokens": 0,
            "token_counts": []  # List of token counts after each interaction
        }
        
        if isinstance(prompt, str):
            # Add initial observation
            initial_content = prompt
            initial_step = TrajectoryStep(
                step_type=StepType.OBSERVATION,
                content=initial_content,  # No "Task:" prefix
                metadata={"prompt": prompt}
            )
            trajectory.add_step(initial_step)
            trajectory.metadata["message_lengths"].append({
                "role": "user",
                "length": len(initial_content),
                "step_type": "observation"
            })
        elif isinstance(prompt, list):
            if prompt[0]["role"] == "system":
                self.custom_system_prompt = prompt[0]["content"]
                prompt = prompt[1: ]
            for message in prompt:
                # Add initial observation
                initial_content = message["content"]
                initial_step = TrajectoryStep(
                    step_type=StepType.OBSERVATION,
                    content=initial_content,  # No "Task:" prefix
                    metadata={"prompt": initial_content}
                )
                trajectory.add_step(initial_step)
                trajectory.metadata["message_lengths"].append({
                    "role": message["role"],
                    "length": len(initial_content),
                    "step_type": "observation"
                })
        else:
            raise Exception("Agent Rollout Error: please use str or list format with prompt.")
        
        consecutive_thoughts = 0
        round_count = 0

        run_start_time = time.time()
        exist_tokens = 0
        start_message_nums = len(self.format_messages_for_llm(trajectory))
        
        loop = asyncio.get_event_loop()

        while self.should_continue(trajectory) and round_count < self.max_rounds:
            try:
                self.trajectory = trajectory
                
                # Generate next step
                messages = self.format_messages_for_llm(trajectory)
                
                # Add steps remaining to the last user message if enabled
                if self.show_steps_remaining:
                    messages = self._add_steps_remaining(messages, round_count)

                
                if max_tokens and tokenizer_func and convert_messages_to_tokens_and_masks:
                    current_add_messages = messages[start_message_nums:]
                    for message in current_add_messages:
                        if message.get("role") == "user":
                            exist_tokens += len(convert_messages_to_tokens_and_masks([message], tokenizer=tokenizer_func, parser=chat_parser, contains_first_msg=False, contains_generation_msg=True)[0])
                        else:
                            exist_tokens += len(convert_messages_to_tokens_and_masks([message], tokenizer=tokenizer_func, parser=chat_parser, contains_first_msg=False, contains_generation_msg=False)[0])
                    start_message_nums = len(messages)
                    remaining_max_tokens = max_tokens - exist_tokens
                else:
                    remaining_max_tokens = self.max_tokens_per_step
                
                # Check if remaining tokens are insufficient for generation
                if remaining_max_tokens <= 0:
                    trajectory.metadata["stop_reason"] = "TRUNCATION"
                    logger.info(f"Trajectory {request_id} truncated: remaining_max_tokens={remaining_max_tokens} <= 0")
                    break

                # Time the LLM generation
                start_time = time.time()
                # Generate response with profiling
                if "temperature" not in kwargs:
                    kwargs["temperature"] =self.temperature
                kwargs["max_tokens"] = remaining_max_tokens

                result = await llm_generate_func(
                    messages=messages,
                    application_id=application_id,
                    idx=idx,
                    **kwargs
                )
                
                # Handle result - for verl it's (response, inf_log_probs), for openai it's just response
                step_inf_log_probs = None
                llm_tokens = []
                if isinstance(result, tuple):
                    if len(result) > 2:
                        print("[SweAgentToolIcepop] get result more than 2")
                        response, step_inf_log_probs, llm_tokens = result[0], result[1], result[2]
                    else:
                        response = result[0]
                else:
                    response = result
                    
                # Record timing
                generation_time = time.time() - start_time
                print(f"LLM call round {round_count + 1} execution time: {generation_time:.3f} seconds, pod name: {request_id}, idx is {idx}, application id is {application_id}")
                
                trajectory.metadata["assistant_timings"].append({
                    "round": round_count + 1,
                    "generation_time_seconds": round(generation_time, 3),
                    "timestamp": datetime.now().isoformat()
                })
                
                # Parse the response (may contain multiple steps)
                parsed_steps = self._parse_react_response(response)
                
                # Process each parsed step
                for step in parsed_steps:
                    trajectory.add_step(step)
                    
                    # Record message length for assistant messages
                    if step.step_type in [StepType.THOUGHT, StepType.ACTION, StepType.FINAL_ANSWER]:
                        trajectory.metadata["message_lengths"].append({
                            "role": "assistant",
                            "length": len(step.content),
                            "step_type": step.step_type.value
                        })
                    
                    # Handle the step based on its type
                    if step.step_type == StepType.ACTION:
                        # Record tool call
                        if step.tool_name:
                            trajectory.metadata["tool_calls"].append(step.tool_name)
                            trajectory.metadata["total_tool_calls"] += 1

                        # Check if this was a termination tool
                        if (step.tool_name and 
                            step.tool_name in self.termination_tool_names):
                            trajectory.is_completed = True
                            if not trajectory.metadata["stop_reason"]:
                                trajectory.metadata["stop_reason"] = "ENV_DONE"
                            break

                        # Execute action
                        result_step = await loop.run_in_executor(executor, self._handle_action, step, trajectory)
                        if result_step:
                            trajectory.add_step(result_step)

                            # Record observation length
                            trajectory.metadata["message_lengths"].append({
                                "role": "user",
                                "length": len(result_step.content),
                                "step_type": "action_result"
                            })

                        round_count += 1
                            
                    elif step.step_type == StepType.FINAL_ANSWER:
                        # Mark trajectory as completed
                        trajectory.is_completed = True
                        if not trajectory.metadata["stop_reason"]:
                            trajectory.metadata["stop_reason"] = "ENV_DONE"
                        break
                    
                    # Break if trajectory is completed
                    if trajectory.is_completed:
                        break
                    
                exec_time = time.time()

                if trajectory_timeout - (exec_time - run_start_time) < 0:
                    trajectory.metadata["stop_reason"] = "TIMEOUT"
                    return trajectory
                
            except Exception as e:
                traceback.print_exc()
                logger.error(f"Error in trajectory {request_id}: {e}")
                trajectory.metadata["error_count"] += 1
                error_step = TrajectoryStep(
                    step_type=StepType.THOUGHT,
                    content=f"I encountered an error: {str(e)}. Let me try a different approach.",
                    metadata={"error": str(e)}
                )
                trajectory.add_step(error_step)
        
        # Set stop reason if not already set
        if not trajectory.metadata["stop_reason"]:
            if round_count >= self.max_rounds:
                trajectory.metadata["stop_reason"] = "MAX_STEPS"
            else:
                trajectory.metadata["stop_reason"] = "ENV_DONE"
        
        # Finalize metadata
        trajectory.metadata["end_time"] = datetime.now().isoformat()
        trajectory.metadata["total_rounds"] = round_count
        trajectory.metadata["total_steps"] = len(trajectory.steps)
        
        # Calculate total execution time
        start_time = datetime.fromisoformat(trajectory.metadata["start_time"])
        end_time = datetime.fromisoformat(trajectory.metadata["end_time"])
        trajectory.metadata["total_execution_time_seconds"] = round((end_time - start_time).total_seconds(), 3)
        
        # Calculate average message lengths
        if trajectory.metadata["message_lengths"]:
            user_lengths = [m["length"] for m in trajectory.metadata["message_lengths"] if m["role"] == "user"]
            assistant_lengths = [m["length"] for m in trajectory.metadata["message_lengths"] if m["role"] == "assistant"]
            
            trajectory.metadata["average_user_message_length"] = round(sum(user_lengths) / len(user_lengths), 2) if user_lengths else 0
            trajectory.metadata["average_assistant_message_length"] = round(sum(assistant_lengths) / len(assistant_lengths), 2) if assistant_lengths else 0
            trajectory.metadata["total_user_chars"] = sum(user_lengths)
            trajectory.metadata["total_assistant_chars"] = sum(assistant_lengths)
        
        # Calculate average assistant response time
        if trajectory.metadata["assistant_timings"]:
            avg_time = sum(t["generation_time_seconds"] for t in trajectory.metadata["assistant_timings"]) / len(trajectory.metadata["assistant_timings"])
            trajectory.metadata["average_assistant_response_time_seconds"] = round(avg_time, 3)
        
        
        self.finalize_trajectory(trajectory)
        logger.info(f"[DEBUG] finalize_trajectory completed for {trajectory.request_id}")
        
        # End trajectory profiling
        if trajectory_event_id:
            logger.info(f"[DEBUG] Ending profiler event for {trajectory.request_id}")
            try:
                profiler.end_event(trajectory_event_id)
                logger.info(f"[DEBUG] Profiler event ended for {trajectory.request_id}")
            except Exception as e:
                logger.warning(f"Error ending profiler event: {e}")
        
        # Add profiler data to trajectory metadata if enabled
        if profiler.enabled and profiler.events:
            logger.info(f"[DEBUG] Getting profiler summary for {trajectory.request_id}")
            try:
                # Add timeout protection for profiler summary generation
                trajectory.metadata["profiler_summary"] = profiler.get_summary()
                logger.info(f"[DEBUG] Profiler summary added for {trajectory.request_id}")
            except Exception as e:
                logger.warning(f"Error getting profiler summary: {e}")
                trajectory.metadata["profiler_summary"] = {"error": str(e)}
        
        logger.info(f"[DEBUG] About to return trajectory for {trajectory.request_id}")
        return trajectory
    
    def _parse_react_response(self, output: str) -> List[TrajectoryStep]:
        """
        Parse LLM response into a list of trajectory steps.
        
        Returns:
            List of TrajectoryStep objects (typically 1-2 steps, including thought + action)
        """
        output = output.strip()
        try:
            # Extract the function name: <function=...>
            fn_match = re.search(r"<function\s*=\s*([^>]+)>", output)
            function_name = fn_match.group(1).strip() if fn_match else ""

            # Extract parameters of the form: <parameter=KEY>VALUE</parameter>
            # DOTALL allows the captured VALUE to span multiple lines
            pattern = r"<parameter\s*=\s*([^>]+)>(.*?)</parameter>"
            param_matches = re.findall(pattern, output, flags=re.DOTALL)
            
            # Assume it's an action
            metadata = {
                "raw_output": output, 
                "custom_parsed": True
            }

            params = {}
            for param_key, param_value in param_matches:
                param_key = param_key.strip()
                param_value = param_value.strip()
                params[param_key] = param_value

            return [TrajectoryStep(
                step_type=StepType.ACTION,
                content=output,  # Keep original LLM output as content
                metadata=metadata,
                tool_name=function_name,
                tool_args=params
            )]
        except Exception as e:
            raise Exception(f"[AgentLogs] Error when parser output as action! Current output is: {output}")

    def _handle_action(self, action_step: TrajectoryStep, trajectory: Trajectory) -> Optional[TrajectoryStep]:
        """Handle action step execution using the existing tool system."""
        # Get profiler instance
        profiler = self.profiler or get_profiler()
        
        if not action_step.tool_name:
            # No valid tool call found
            return TrajectoryStep(
                step_type=StepType.ACTION_RESULT,
                content=f"Invalid action format: '{action_step.content}'. Please use: tool_name(param=value)",
                metadata={"action_failed": True}
            )
        
        # Debug: Print tool execution details
        if self.debug:
            print("\n" + "="*80)
            print(f"🔧 [DEBUG] Tool Execution")
            print(f"   Tool Name: {action_step.tool_name}")
            print(f"   Tool Args: {json.dumps(action_step.tool_args or {}, indent=2)}")
            print("="*80)
        
        # Use the existing execute_tool_call method from BaseAgent with profiling
        tool_event_type = EventType.TOOL_EXECUTION
        
        result_step = self.execute_tool_call(
            action_step.tool_name,
            action_step.tool_args or {},
            trajectory
        )
        
        # Debug: Print tool result
        if self.debug:
            print("\n" + "="*80)
            print(f"📤 [DEBUG] Tool Result")
            print(f"   Tool: {action_step.tool_name}")
            if result_step:
                print(f"   Success: {not result_step.metadata.get('action_failed', False)}")
                print(f"   Result: {result_step.content}")
            else:
                print(f"   Result: None (no result returned)")
            print("="*80)
        
        return result_step
    
    def to_bashcmd(self, function_name: str, parameters: Dict[str, Any]) -> str:
        """
        Convert a function call to a bash command string.
        
        Args:
            function_name: Name of the function/tool to execute
            parameters: Dictionary of parameters for the function
            
        Returns:
            str: Formatted bash command string with properly quoted arguments
        """
        # Start building the command
        cmd_parts = [shlex.quote(function_name)]

        # If there's a 'command' parameter, put that next
        base_command = parameters.get("command")
        if base_command is not None:
            cmd_parts.append(shlex.quote(base_command))

        # Append all other parameters
        for param_key, param_value in parameters.items():
            if param_key == "command":
                continue

            # Safely quote the param_value
            param_value_quoted = shlex.quote(str(param_value))
            cmd_parts.append(f"--{param_key}")
            cmd_parts.append(param_value_quoted)

        return " ".join(cmd_parts)

    
    def execute_tool_call(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        trajectory: Trajectory
    ) -> TrajectoryStep:
        """
        Execute a tool call and return the result step.
        
        Args:
            tool_name: Name of the tool to call
            tool_args: Arguments for the tool
            trajectory: Current trajectory
            
        Returns:
            Step containing tool execution result
        """
        try:
            if tool_name == "finish" or tool_name == "submit":
                output = "<<< Finished >>>"
            else:
                bash_cmd = self.to_bashcmd(tool_name, tool_args)
                output = self._exec_command(bash_cmd)["stdout"]
            result_step = TrajectoryStep(
                step_type=StepType.ACTION_RESULT,
                content=f"Execution output of [{tool_name}]:\n{str(output)}",
                metadata={
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "execution_successful": True
                },
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=output
            )
            logger.info(f"Tool {tool_name} executed successfully for trajectory {trajectory.request_id}")
            return result_step
            
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            error_step = TrajectoryStep(
                step_type=StepType.ACTION_RESULT,
                content=f"Execution output of [{tool_name}]:\nError: {str(e)}",
                metadata={
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "execution_successful": False,
                    "error": str(e)
                },
                tool_name=tool_name,
                tool_args=tool_args
            )
            return error_step
        
    def _get_k8s_manager(self):
        """Get or create K8s manager instance."""
        if self.k8s_manager is None:
            self.k8s_manager = KubernetesManager(
                namespace=self.namespace,
                kubeconfig_path=self.kubeconfig_path
            )
        return self.k8s_manager
    
    def _exec_command(self, command: str) -> Dict[str, Any]:
        """Execute command in K8s pod."""
        try:
            k8s_mgr = self._get_k8s_manager()
            if not self.timeout:
                timeout = 120
            # Prepend cd to working directory for K8s execution
            # Note: File operations typically don't need timeout, but we add cd for consistency
            full_command = f"cd {self.working_dir} && timeout {timeout} {command}"
            logger.debug(f"Executing command in pod {self.pod_name}: {full_command}")
            output, exit_code = k8s_mgr.execute_command(self.pod_name, full_command)
            
            # Convert exit_code to int if it's a string
            if isinstance(exit_code, str):
                # Handle "Error: Exit code X" format
                if "Exit code" in exit_code:
                    try:
                        # Extract number from "Error: Exit code 2"
                        exit_code_int = int(exit_code.split("Exit code")[-1].strip())
                    except:
                        exit_code_int = -1
                elif exit_code.isdigit():
                    exit_code_int = int(exit_code)
                else:
                    exit_code_int = -1
            else:
                exit_code_int = exit_code
            
            # Return consistent format
            return {
                "success": exit_code_int == 0,
                "stdout": output if output else "",
                "stderr": "" if exit_code_int == 0 else f"Command failed with exit code {exit_code_int}",
                "exit_code": exit_code_int
            }
        except Exception as e:
            logger.error(f"K8s command execution failed: {e}", exc_info=True)
            error_str = str(e)
            # Check if it's an ApiException
            if "ApiException" in error_str:
                print(f"K8s API error - pod {self.pod_name} may not be ready or connection issue")
            return {
                "success": False,
                "stdout": "",
                "stderr": error_str,
                "exit_code": -1
            }

__all__ = ['SweAgent']