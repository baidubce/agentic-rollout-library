# workers/tools/cc_tools/bash_tool/k8s_bash_tool.py
import asyncio
import logging
import posixpath
import re
import shlex
from typing import Any, Dict, List, Optional, Tuple

try:
    from kodo import KubernetesManager
except ImportError:  # pragma: no cover
    KubernetesManager = None

from workers.core.cc_tool_base import CCToolBase
from workers.core.tool_schemas import (
    OpenAIFunctionToolSchema,
    ToolResult,
    create_openai_tool_schema,
)

logger = logging.getLogger(__name__)


def _split_commands(cmd: str) -> List[str]:
    parts = re.split(r"\s*(?:;|&&|\|\|)\s*", cmd.strip())
    return [p for p in parts if p]


def _normalize_posix(p: str) -> str:
    return posixpath.normpath(p or "/")


def _is_child_path(base: str, target: str) -> bool:
    base_n = _normalize_posix(base)
    tgt_n = _normalize_posix(target)
    if not tgt_n.startswith("/"):
        return False
    if base_n == "/":
        return True
    return tgt_n == base_n or tgt_n.startswith(base_n.rstrip("/") + "/")


def _coerce_rc(rc: Any) -> int:
    try:
        return int(rc)
    except Exception:
        return -1


class K8sBashTool(CCToolBase):
    DEFAULT_BANNED_COMMANDS = [
        "git",
        "ipython",
        "jupyter",
        "nohup",
        "sudo",
        "rm",
        "shutdown",
        "reboot",
        "passwd",
        "su",
        "chmod",
    ]

    DANGEROUS_PATTERNS = [
        "rm -rf /",
        ":(){ :|:& };:",
        "chmod -R 777",
        "> /dev/sda",
        "dd if=/dev/zero",
        "wget | sh",
        "curl | sh",
        "|sh",
        "|bash",
    ]

    def __init__(self, config: Optional[Dict[str, Any]] = None, tool_schema: Optional[OpenAIFunctionToolSchema] = None):
        if KubernetesManager is None:
            raise ImportError("kodo library is required for K8s tools.")
        cfg = config or {}

        self.pod_name: str = cfg.get("pod_name", "target-pod")
        self.namespace: str = cfg.get("namespace", "default")
        self.kubeconfig_path: Optional[str] = cfg.get("kubeconfig_path")

        self.original_workdir: str = cfg.get("original_workdir", "/workspace")
        self.default_timeout_ms: int = int(cfg.get("timeout_ms", 120_000))
        self.allow_dangerous: bool = bool(cfg.get("allow_dangerous", False))
        self.banned_commands = set(cfg.get("blocked_commands", self.DEFAULT_BANNED_COMMANDS))

        self._instances: Dict[str, Dict[str, Any]] = {}
        self._k8s_manager: Optional[KubernetesManager] = None

        super().__init__(cfg, tool_schema)

    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        return create_openai_tool_schema(
            name="k8s_bash",
            description=f"Execute shell commands in Kubernetes pod '{self.pod_name}'.",
            parameters={
                "command": {"type": "string", "description": "The shell command to execute inside the pod"},
                "working_directory": {"type": "string", "description": "Optional working directory. Defaults to original_workdir."},
                "timeout_ms": {"type": "number", "description": "Optional timeout in milliseconds"},
            },
            required=["command"],
        )

    async def _initialize_instance(self, instance_id: str, **kwargs) -> None:
        self._instances[instance_id] = {"cwd": self.original_workdir, "cwd_reset": 0, "history": []}

    async def _cleanup_instance(self, instance_id: str, **kwargs) -> None:
        if instance_id in self._instances:
            del self._instances[instance_id]

    def _get_k8s_manager(self) -> KubernetesManager:
        if self._k8s_manager is None:
            self._k8s_manager = KubernetesManager(namespace=self.namespace, kubeconfig_path=self.kubeconfig_path)
        return self._k8s_manager

    def _format_counts(self, text: str) -> Tuple[int, int]:
        lines = 0 if not text else text.count("\n") + (0 if text.endswith("\n") else 1)
        length = len(text or "")
        return lines, length

    def _security_check(self, command_raw: str, working_dir: str) -> Optional[str]:
        for pat in self.DANGEROUS_PATTERNS:
            if not self.allow_dangerous and pat.lower() in command_raw.lower():
                return f"Command contains dangerous pattern: {pat}"

        parts = _split_commands(command_raw)
        for cmd in parts:
            base = (cmd.strip().split() or [""])[0].lower()
            if base in self.banned_commands:
                return f"Command '{base}' is not allowed for security reasons"

            if base == "cd":
                target = cmd.strip().split(maxsplit=1)
                if len(target) >= 2:
                    dest = target[1].strip().strip("'").strip('"')
                    dest_full = dest if dest.startswith("/") else posixpath.join(working_dir, dest)
                    dest_full = _normalize_posix(dest_full)
                    if not _is_child_path(self.original_workdir, dest_full):
                        return (
                            f"ERROR: cd to '{dest_full}' was blocked. For security, this tool may only change "
                            f"directories to children of the original working directory ({self.original_workdir})."
                        )
        return None

    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        try:
            if "command" not in parameters:
                return ToolResult(success=False, error="'command'")

            command_raw = str(parameters["command"]).strip()
            if not command_raw:
                result = self._build_result("", "/bin/sh: syntax error: unexpected end of file", 2, command_raw, False,
                                            self._instances.get(instance_id, {}).get("cwd", self.original_workdir),
                                            self.default_timeout_ms)
                metrics = self._build_metrics(2, result["stdout"], result["stderr"],
                                              result["cwd"], result["timeout_ms"])
                return ToolResult(success=False, result=result, error="Empty command", metrics=metrics)

            try:
                timeout_ms = int(parameters.get("timeout_ms", self.default_timeout_ms))
            except Exception as e:
                return ToolResult(success=False, error=str(e))

            workdir = str(parameters.get("working_directory") or self._instances.get(instance_id, {}).get("cwd", self.original_workdir))
            workdir = _normalize_posix(workdir or self.original_workdir)

            sec_err = self._security_check(command_raw, workdir)
            if sec_err:
                return ToolResult(success=False, error=sec_err)

            inner = f"cd {shlex.quote(workdir)} && {command_raw}"
            shell_cmd = f"/bin/sh -lc {shlex.quote(inner)}"

            try:
                res = await asyncio.wait_for(self._run_k8s_command(shell_cmd, timeout_ms), timeout=timeout_ms / 1000.0)
                rc = _coerce_rc(res.get("return_code", -1))
                out = res.get("stdout", "") or ""
                err_raw = res.get("stderr", "") or ""
                err = "" if rc == 0 else (err_raw.rstrip("\n") + ("" if err_raw.endswith("\n") else "\n") + f"Exit code {rc}" if err_raw else f"Exit code {rc}")

                result = self._build_result(out, err, rc, command_raw, False, workdir, timeout_ms)
                metrics = self._build_metrics(rc, out, err, workdir, timeout_ms)

                if rc == 0:
                    return ToolResult(success=True, result=result, metrics=metrics)
                else:
                    return ToolResult(success=False, error=f"Command failed with return code {rc}", result=result, metrics=metrics)

            except asyncio.TimeoutError:
                rc = 124
                out = ""
                err = "Exit code 124"
                result = self._build_result(out, err, rc, command_raw, True, workdir, timeout_ms)
                metrics = self._build_metrics(rc, out, err, workdir, timeout_ms)
                return ToolResult(success=False, error=f"Timeout after {timeout_ms} ms", result=result, metrics=metrics)

        except Exception as e:
            logger.error("K8sBashTool execution failed", exc_info=True)
            return ToolResult(success=False, error=str(e))

    def _build_result(self, stdout: str, stderr: str, rc: Any, command: str, interrupted: bool, cwd: str, timeout_ms: int) -> Dict[str, Any]:
        stdout_lines, _ = self._format_counts(stdout)
        stderr_lines, _ = self._format_counts(stderr)
        return {
            "stdout": stdout,
            "stdout_lines": stdout_lines,
            "stderr": stderr,
            "stderr_lines": stderr_lines,
            "interrupted": interrupted,
            "return_code": rc,
            "command": command,
            "cwd": cwd,
            "pod_name": self.pod_name,
            "namespace": self.namespace,
            "timeout_ms": timeout_ms,
            "execution_location": f"{self.namespace}/{self.pod_name}",
        }

    def _build_metrics(self, rc: Any, stdout: str, stderr: str, cwd: str, timeout_ms: int) -> Dict[str, Any]:
        _, out_len = self._format_counts(stdout)
        _, err_len = self._format_counts(stderr)
        return {
            "return_code": rc,
            "stdout_length": out_len,
            "stderr_length": err_len,
            "execution_location": f"{self.namespace}/{self.pod_name}",
            "cwd": cwd,
            "cwd_reset": 0,
            "timeout_ms": timeout_ms,
        }

    async def _run_k8s_command(self, command: str, timeout_ms: int) -> Dict[str, Any]:
        mgr = self._get_k8s_manager()
        last_err: Optional[Exception] = None

        for attempt in (1, 2):
            try:
                try:
                    result = mgr.execute_command(self.pod_name, command, timeout_ms=timeout_ms)
                except TypeError:
                    result = mgr.execute_command(self.pod_name, command)

                stdout, stderr, rc = "", "", None
                if isinstance(result, tuple):
                    if len(result) == 3:
                        stdout, stderr, rc = result
                    elif len(result) == 2:
                        stdout, rc = result
                        stderr = ""
                    else:
                        stdout = str(result)
                        stderr = ""
                        rc = -1
                else:
                    stdout = getattr(result, "stdout", "")
                    stderr = getattr(result, "stderr", "")
                    rc = getattr(result, "return_code", None)

                return {
                    "stdout": stdout if isinstance(stdout, str) else str(stdout),
                    "stderr": stderr if isinstance(stderr, str) else str(stderr),
                    "return_code": _coerce_rc(rc),
                }
            except Exception as e:
                last_err = e
                if attempt == 1:
                    await asyncio.sleep(0.3)
                    continue
                logger.warning("K8s exec failed after retry: %r", e)
                return {"stdout": f"Error: {repr(last_err)}", "stderr": "", "return_code": -1}
