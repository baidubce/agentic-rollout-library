from __future__ import annotations
import asyncio
import logging
import os
import posixpath
import shlex
import time
from typing import Any, Dict, List, Optional, Tuple
from workers.core.enhanced_base_tool import CCToolBase
from workers.core.tool_schemas import OpenAIFunctionToolSchema, ToolResult, create_openai_tool_schema

logger = logging.getLogger(__name__)

try:
    from kodo import KubernetesManager
except ImportError:
    KubernetesManager = None

MAX_FILES = 1000
TRUNCATED_MESSAGE = (
    f"There are more than {MAX_FILES} files in the repository. "
    "Use the LS tool (passing a specific path), Bash tool, and other tools to explore nested directories. "
    f"The first {MAX_FILES} files and directories are included below:\n\n"
)

def _is_absolute(p: str) -> bool:
    return p.startswith("/")

def _is_hidden_path(p: str) -> bool:
    p2 = p[:-1] if p.endswith("/") else p
    for seg in p2.split("/"):
        if seg == "":
            continue
        if seg.startswith("."):
            return True
    return False

def _skip(p: str) -> bool:
    if _is_hidden_path(p):
        return True
    if "/__pycache__/" in (p if p.endswith("/") else p + "/"):
        return True
    return False

def _create_file_tree(sorted_rel_paths: List[str], sep: str = "/") -> List[Dict[str, Any]]:
    root: List[Dict[str, Any]] = []
    for rel in sorted_rel_paths:
        parts = rel.split(sep)
        current_level = root
        current_path = ""
        for i, part in enumerate(parts):
            if not part:
                continue
            current_path = f"{current_path}{sep}{part}" if current_path else part
            is_last = i == len(parts) - 1
            existing = next((n for n in current_level if n["name"] == part), None)
            if existing:
                current_level = existing.get("children", [])
            else:
                node: Dict[str, Any] = {"name": part, "path": current_path, "type": "file" if is_last and not rel.endswith(sep) else "directory"}
                if node["type"] == "directory":
                    node["children"] = []
                current_level.append(node)
                current_level = node.get("children", [])
    return root

def _print_tree(tree: List[Dict[str, Any]], root_abs_path: str, sep: str = "/") -> str:
    lines: List[str] = [f"- {root_abs_path.rstrip(sep)}{sep}"]
    def _recurse(nodes: List[Dict[str, Any]], prefix: str) -> None:
        for node in nodes:
            is_dir = node["type"] == "directory"
            lines.append(f"{prefix}- {node['name']}{sep if is_dir else ''}")
            if is_dir and node.get("children"):
                _recurse(node["children"], prefix + "  ")
    _recurse(tree, "  ")
    return "\n".join(lines) + "\n"

class K8sLSTool(CCToolBase):
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if KubernetesManager is None:
            raise ImportError("kodo is required for K8s-backed tools.")
        cfg = config or {}
        self.pod_name: str = str(cfg.get("pod_name", "target-pod"))
        self.namespace: str = str(cfg.get("namespace", "default"))
        self.container: Optional[str] = cfg.get("container")
        self.kubeconfig_path: Optional[str] = cfg.get("kubeconfig_path") or self._resolve_kubeconfig()
        self.timeout: float = float(cfg.get("timeout", 30))
        super().__init__(cfg)
        self._k8s: Optional[KubernetesManager] = None
        self.execution_history: Dict[str, List[Dict[str, Any]]] = {}

    def _resolve_kubeconfig(self) -> Optional[str]:
        for env in ("K8S_KUBECONFIG_PATH", "KUBECONFIG"):
            p = os.environ.get(env)
            if p and os.path.exists(p):
                return p
        default = os.path.expanduser("~/.kube/config")
        return default if os.path.exists(default) else None

    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        return create_openai_tool_schema(
            name="LS",
            description="Recursively list a directory tree inside the Kubernetes pod.",
            parameters={"path": {"type": "string", "description": "The absolute path to the directory to list (must be absolute, not relative)"}},
            required=["path"],
        )

    async def _initialize_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history[instance_id] = []

    async def _cleanup_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history.pop(instance_id, None)

    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        t0 = time.time()
        try:
            if "path" not in parameters:
                raise ValueError("Invalid input: 'path' is required")
            path = parameters["path"]
            if not isinstance(path, str):
                raise ValueError("Invalid input: 'path' must be a string")
            if not _is_absolute(path):
                raise ValueError("Invalid input: 'path' must be absolute")
            if _is_hidden_path(path):
                duration_ms = int((time.time() - t0) * 1000)
                root_line = f"- {path.rstrip('/')}/\n"
                return ToolResult(
                    success=True,
                    result=root_line,
                    metrics={
                        "duration_ms": duration_ms,
                        "execution_location": f"{self.namespace}/{self.pod_name}",
                        "rc": 0,
                        "stdout_size": 0,
                        "num_items": 0,
                        "truncated": False,
                        "in_pod_command": "(skipped due to hidden root)",
                    },
                )

            sep_marker = "__SEP_DIR_FILE__"
            abs_quoted = shlex.quote(path)
            user_cmd = (
                f'target={abs_quoted}; '
                f'if [ ! -d "$target" ]; then echo "Not a directory: $target" >&2; exit 2; fi; '
                f'if find --help 2>&1 | grep -q mindepth; then '
                f'find "$target" -mindepth 1 -type d -print 2>/dev/null; '
                f'echo {shlex.quote(sep_marker)}; '
                f'find "$target" -mindepth 1 -type f -print 2>/dev/null; '
                f'else '
                f'find "$target" -type d -print 2>/dev/null | sed "1d"; '
                f'echo {shlex.quote(sep_marker)}; '
                f'find "$target" -type f -print 2>/dev/null; '
                f'fi'
            )

            stdout, rc = await self._run_in_pod(user_cmd)
            try:
                rc_int = int(rc)
            except Exception:
                rc_int = -1

            duration_ms = int((time.time() - t0) * 1000)
            metrics_base = {
                "duration_ms": duration_ms,
                "execution_location": f"{self.namespace}/{self.pod_name}",
                "rc": rc_int,
                "stdout_size": len(stdout),
                "in_pod_command": 'sh -lc ' + shlex.quote(user_cmd),
            }

            if rc_int != 0:
                fb_cmd = (
                    f'target={abs_quoted}; '
                    f'if [ ! -d "$target" ]; then echo "Not a directory: $target" >&2; exit 2; fi; '
                    f'ls -1A "$target" 2>/dev/null | while IFS= read -r n; do '
                    f'if [ -d "$target/$n" ]; then echo "$n/"; else echo "$n"; fi; done'
                )
                fb_out, fb_rc = await self._run_in_pod(fb_cmd)
                try:
                    fb_rc_int = int(fb_rc)
                except Exception:
                    fb_rc_int = -1
                if fb_rc_int == 0:
                    names = [ln.strip() for ln in fb_out.splitlines() if ln.strip()]
                    rel_items = []
                    for name in names:
                        if name.startswith("."):
                            continue
                        rel_items.append(name)
                    rel_items_sorted = sorted(rel_items)
                    tree = _create_file_tree(rel_items_sorted, sep="/")
                    tree_text = _print_tree(tree, root_abs_path=path, sep="/")
                    return ToolResult(
                        success=True,
                        result=tree_text,
                        metrics={**metrics_base, "rc": 0, "num_items": len(rel_items_sorted), "truncated": False, "fallback": "ls_top_level"},
                    )
                return ToolResult(success=False, error=f"In-pod command failed with rc={rc_int}.", metrics=metrics_base)

            if sep_marker in stdout:
                dir_part, file_part = stdout.split(sep_marker, 1)
                dir_lines = [ln.strip() for ln in dir_part.strip().splitlines() if ln.strip()]
                file_lines = [ln.strip() for ln in file_part.strip().splitlines() if ln.strip()]
            else:
                dir_lines, file_lines = [], [ln.strip() for ln in stdout.strip().splitlines() if ln.strip()]

            dir_paths_abs = [ln if ln.endswith("/") else ln + "/" for ln in dir_lines]
            file_paths_abs = file_lines

            rel_items: List[str] = []
            for p in dir_paths_abs + file_paths_abs:
                if _skip(p):
                    continue
                rel = posixpath.relpath(p, start=path)
                if p.endswith("/") and not rel.endswith("/"):
                    rel = rel.rstrip("/") + "/"
                rel_items.append(rel)

            rel_items_sorted = sorted(rel_items)
            truncated = len(rel_items_sorted) > MAX_FILES
            if truncated:
                rel_items_sorted = rel_items_sorted[:MAX_FILES]

            tree = _create_file_tree(rel_items_sorted, sep="/")
            tree_text = _print_tree(tree, root_abs_path=path, sep="/")
            output = (TRUNCATED_MESSAGE + tree_text) if truncated else tree_text

            return ToolResult(
                success=True,
                result=output,
                metrics={**metrics_base, "num_items": len(rel_items_sorted), "truncated": truncated},
            )

        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Timeout after {self.timeout}s", metrics={"rc": 124})
        except Exception as e:
            logger.exception("K8sLSTool execution failed")
            return ToolResult(success=False, error=f"Exec channel failed: {type(e).__name__}: {e}", metrics={"rc": -1})

    def _mgr(self) -> KubernetesManager:
        if self._k8s is None:
            self._k8s = KubernetesManager(namespace=self.namespace, kubeconfig_path=self.kubeconfig_path)
        return self._k8s

    async def _exec_once(self, shell_cmd: str) -> Tuple[str, int]:
        def _call():
            mgr = self._mgr()
            if self.container:
                try:
                    out, code = mgr.execute_command(self.pod_name, shell_cmd, container=self.container)  # type: ignore[call-arg]
                except TypeError:
                    out, code = mgr.execute_command(self.pod_name, shell_cmd)
                else:
                    if str(code) == "-1":
                        try:
                            out2, code2 = mgr.execute_command(self.pod_name, shell_cmd)
                            return out2, code2
                        except Exception:
                            pass
                    return out, code
            return mgr.execute_command(self.pod_name, shell_cmd)
        out, code = await asyncio.wait_for(asyncio.to_thread(_call), timeout=self.timeout)
        try:
            code = int(code)
        except Exception:
            code = -1
        return str(out), code

    async def _run_in_pod(self, user_cmd: str) -> Tuple[str, int]:
        shell_cmd = f"sh -lc {shlex.quote(user_cmd)}"
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                return await self._exec_once(shell_cmd)
            except asyncio.TimeoutError:
                raise
            except Exception as e:
                last_exc = e
                await asyncio.sleep(min(0.2 * (2 ** attempt), 1.0))
        raise RuntimeError(f"exec_failed: {type(last_exc).__name__}: {last_exc}")
