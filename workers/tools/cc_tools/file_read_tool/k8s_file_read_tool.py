from __future__ import annotations

import asyncio
import base64
import logging
import math
import os
import re
import shlex
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

try:
    from kodo import KubernetesManager
except ImportError:
    KubernetesManager = None  # type: ignore

from workers.core.cc_tool_base import CCToolBase
from workers.core.tool_schemas import (
    OpenAIFunctionToolSchema,
    ToolResult,
    create_openai_tool_schema,
)

logger = logging.getLogger(__name__)

MAX_OUTPUT_SIZE = int(0.25 * 1024 * 1024)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
MAX_WIDTH = 2000
MAX_HEIGHT = 2000
MAX_IMAGE_SIZE = int(3.75 * 1024 * 1024)
DEFAULT_TIMEOUT_SEC = 30
ALLOWED_ROOT_DEFAULT = "/app"


@dataclass
class _CmdResult:
    stdout: str
    return_code: int


class K8sFileReadTool(CCToolBase):
    def __init__(self, config: Optional[Dict[str, Any]] = None, tool_schema: Optional[OpenAIFunctionToolSchema] = None):
        if KubernetesManager is None:
            raise ImportError("kodo library is required for K8s tools. Please install it before using K8sFileReadTool.")
        config = config or {}
        self.pod_name: str = config.get("pod_name", "default-pod")
        self.namespace: str = config.get("namespace", "default")
        self.kubeconfig_path: Optional[str] = config.get("kubeconfig_path")
        self.timeout: float = float(config.get("timeout", DEFAULT_TIMEOUT_SEC))
        self.container: Optional[str] = config.get("container")
        self.allow_dangerous: bool = bool(config.get("allow_dangerous", False))
        self.allowed_root: str = config.get("allowed_root", ALLOWED_ROOT_DEFAULT)
        super().__init__(config, tool_schema)
        self._k8s_mgr: Optional[KubernetesManager] = None
        self.execution_history: Dict[str, list] = {}

    def is_read_only(self) -> bool:
        """文件读取工具是只读的"""
        return True
    
    def _get_parameters_schema(self) -> Dict[str, Any]:
        """获取参数schema"""
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read (e.g., /testbed/main.py)"
                },
                "offset": {
                    "type": "integer",
                    "description": "Line offset to start reading from (0-based)",
                    "minimum": 0
                },
                "limit": {
                    "type": "integer", 
                    "description": "Maximum number of lines to read",
                    "minimum": 1
                }
            },
            "required": ["file_path"]
        }

    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        return create_openai_tool_schema(
            name="k8s_file_read",
            description=(
                "Read a file (text or image) from within a Kubernetes pod. "
                f"Executes inside '{self.namespace}/{self.pod_name}'. "
                "For large text files, provide 'offset' and 'limit' to page through content. "
                "Images are returned as base64; the tool may attempt in-pod resize/compress."
            ),
            parameters={
                "file_path": {
                    "type": "string",
                    "description": "Absolute path of the file to read (inside the pod).",
                },
                "offset": {
                    "type": "number",
                    "description": "Line number to start reading from (text files). Use 0 to start from the beginning.",
                },
                "limit": {
                    "type": "number",
                    "description": "Number of lines to read (text files). Optional.",
                },
            },
            required=["file_path"],
        )

    async def _initialize_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history[instance_id] = []

    async def _cleanup_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history.pop(instance_id, None)

    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        t0 = time.time()
        try:
            file_path_raw = parameters.get("file_path")
            if not isinstance(file_path_raw, str) or not file_path_raw.strip():
                return ToolResult(success=False, error="`file_path` must be a non-empty string.")
            if not self.allow_dangerous and self._has_injection_tokens(file_path_raw):
                return ToolResult(success=False, error="`file_path` contains disallowed characters.")
            file_path = self._normalize_path(file_path_raw)
            if not self._is_within_allowed_root(file_path):
                return ToolResult(success=False, error="Access to the requested path is not allowed.")

            offset = parameters.get("offset", None)
            limit = parameters.get("limit", None)
            if offset is not None and (not isinstance(offset, (int, float)) or offset < 0):
                return ToolResult(success=False, error="`offset` must be a non-negative number.")
            if limit is not None and (not isinstance(limit, (int, float)) or limit <= 0):
                return ToolResult(success=False, error="`limit` must be a positive number.")

            exists = await self._file_exists(file_path)
            if not exists:
                suggestion = await self._find_similar_file(file_path)
                msg = "File does not exist."
                if suggestion:
                    msg += f" Did you mean {suggestion}?"
                return ToolResult(success=False, error=msg)

            readable = await self._file_readable(file_path)
            if not readable:
                return ToolResult(success=False, error="File is not readable or permission denied.")

            ext = os.path.splitext(file_path.lower())[1]
            if ext in IMAGE_EXTENSIONS:
                data, rc = await self._read_image_payload(file_path, ext)
                duration_ms = int((time.time() - t0) * 1000)
                self._append_history(instance_id, {"action": "read_image", "file_path": file_path, "namespace": self.namespace, "pod_name": self.pod_name, "return_code": rc, "duration_ms": duration_ms})
                if rc != 0:
                    return ToolResult(success=False, error="Failed to read image content from pod.", result=data)
                return ToolResult(
                    success=True,
                    result=data,
                    metrics={"duration_ms": duration_ms, "execution_location": f"{self.namespace}/{self.pod_name}", "payload_size": len(data.get("file", {}).get("base64", ""))},
                )

            size_bytes, rc = await self._file_size(file_path)
            if rc != 0:
                return ToolResult(success=False, error="Failed to stat file size.")
            if size_bytes > MAX_OUTPUT_SIZE and offset is None and limit is None:
                return ToolResult(success=False, error=self._format_file_size_error(size_bytes))

            effective_offset = 1 if offset is None else int(offset)
            content, line_count_window, total_lines, rc = await self._read_text_window(file_path, effective_offset, limit)
            if rc != 0:
                return ToolResult(success=False, error="Failed to read text content from pod.")
            if len(content.encode("utf-8")) > MAX_OUTPUT_SIZE:
                return ToolResult(success=False, error=self._format_file_size_error(len(content)))

            data = {"type": "text", "file": {"filePath": file_path, "content": content, "numLines": int(line_count_window), "startLine": int(effective_offset), "totalLines": int(total_lines)}}
            duration_ms = int((time.time() - t0) * 1000)
            self._append_history(instance_id, {"action": "read_text", "file_path": file_path, "namespace": self.namespace, "pod_name": self.pod_name, "return_code": 0, "duration_ms": duration_ms, "window": {"offset": effective_offset, "limit": int(limit) if limit else None}})
            return ToolResult(success=True, result=data, metrics={"duration_ms": duration_ms, "execution_location": f"{self.namespace}/{self.pod_name}", "stdout_length": len(content), "total_lines": int(total_lines)})
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Timeout after {self.timeout}s")
        except Exception as e:
            logger.error("K8sFileReadTool execution failed: %s", e)
            return ToolResult(success=False, error=str(e))

    def _k8s(self) -> KubernetesManager:
        if self._k8s_mgr is None:
            self._k8s_mgr = KubernetesManager(namespace=self.namespace, kubeconfig_path=self.kubeconfig_path)
        return self._k8s_mgr

    def _normalize_path(self, p: str) -> str:
        p = p.strip()
        if not p.startswith("/"):
            p = f"/{p}".replace("//", "/")
        return os.path.normpath(p)

    def _is_within_allowed_root(self, p: str) -> bool:
        root = os.path.normpath(self.allowed_root or "/")
        pnorm = os.path.normpath(p)
        if root == "/":
            return True
        return pnorm == root or pnorm.startswith(root + "/")

    def _has_injection_tokens(self, s: str) -> bool:
        return any(tok in s for tok in [";", "|", "&", "`", "$(", ")", "<", ">", "\n", "\r"])

    async def _run_cmd(self, command: str) -> _CmdResult:
        k8s = self._k8s()
        shell_cmd = f"sh -lc {shlex.quote(command)}"

        def _call() -> Tuple[str, int]:
            return k8s.execute_command(self.pod_name, shell_cmd)

        try:
            stdout, rc = await asyncio.wait_for(asyncio.to_thread(_call), timeout=self.timeout)
            return _CmdResult(stdout=stdout if isinstance(stdout, str) else str(stdout), return_code=int(rc))
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            logger.error("Pod command failed: %s (cmd=%r)", e, command)
            return _CmdResult(stdout=str(e), return_code=-1)

    async def _file_exists(self, path: str) -> bool:
        res = await self._run_cmd(f"test -e {shlex.quote(path)}; echo $?")
        try:
            return int(res.stdout.strip().splitlines()[-1]) == 0
        except Exception:
            return False

    async def _file_readable(self, path: str) -> bool:
        res = await self._run_cmd(f"test -r {shlex.quote(path)}; echo $?")
        try:
            return int(res.stdout.strip().splitlines()[-1]) == 0
        except Exception:
            return False

    async def _file_size(self, path: str) -> Tuple[int, int]:
        q = shlex.quote(path)
        res1 = await self._run_cmd(f"stat -c %s {q} 2>/dev/null")
        size = self._parse_int(res1.stdout)
        if res1.return_code == 0 and size is not None:
            return int(size), 0
        res2 = await self._run_cmd(f"wc -c < {q} 2>/dev/null")
        size2 = self._parse_int(res2.stdout)
        if res2.return_code == 0 and size2 is not None:
            return int(size2), 0
        return 0, 1

    async def _wc_lines(self, path: str) -> Tuple[int, int]:
        q = shlex.quote(path)
        res = await self._run_cmd(f"wc -l < {q} 2>/dev/null")
        n = self._parse_int(res.stdout)
        if res.return_code == 0 and n is not None:
            return int(n), 0
        return 0, 1

    async def _read_text_window(self, path: str, offset: int, limit: Optional[int]) -> Tuple[str, int, int, int]:
        total_lines, rc = await self._wc_lines(path)
        if rc != 0:
            return "", 0, 0, rc
        start = 1 if offset == 0 else max(1, int(offset))
        if limit is None:
            cmd = f"sed -n '{start},$p' {shlex.quote(path)}"
        else:
            end = max(start, start + int(limit) - 1)
            cmd = f"sed -n '{start},{end}p' {shlex.quote(path)}"
        res = await self._run_cmd(cmd)
        if res.return_code != 0:
            return "", 0, total_lines, res.return_code
        content = res.stdout
        line_count = 0 if not content else len(content.splitlines())
        return content, line_count, total_lines, 0

    async def _find_similar_file(self, path: str) -> Optional[str]:
        dirname = os.path.dirname(path) or "/"
        base = os.path.splitext(os.path.basename(path))[0]
        if not base:
            return None
        for ext in sorted(IMAGE_EXTENSIONS):
            cand = os.path.join(dirname, base + ext)
            if await self._file_exists(cand):
                return cand
        res = await self._run_cmd(f"ls -1 {shlex.quote(dirname)} 2>/dev/null | grep -i '^{re.escape(base)}\\.' | head -n 1")
        hint = res.stdout.strip().splitlines()[0] if res.return_code == 0 and res.stdout.strip() else None
        return os.path.join(dirname, hint) if hint else None

    async def _read_image_payload(self, path: str, ext: str) -> Tuple[Dict[str, Any], int]:
        size_bytes, rc = await self._file_size(path)
        if rc != 0:
            return {}, rc
        width, height = await self._image_dimensions(path)
        if (size_bytes <= MAX_IMAGE_SIZE) and ((width is None and height is None) or (width is not None and height is not None and width <= MAX_WIDTH and height <= MAX_HEIGHT)):
            b64, rc0 = await self._base64_file(path)
            if rc0 != 0 or not b64:
                return {}, 1
            return self._image_payload(b64, ext), 0
        payload, rc1 = await self._resize_or_compress_image(path, ext)
        if rc1 == 0:
            return payload, 0
        b64, rc2 = await self._base64_file(path)
        if rc2 != 0 or not b64:
            return {}, 1
        return self._image_payload(b64, ext), 0

    async def _image_dimensions(self, path: str) -> Tuple[Optional[int], Optional[int]]:
        res = await self._run_cmd(f"command -v identify >/dev/null 2>&1 && identify -format '%w %h' {shlex.quote(path)}")
        if res.return_code != 0 or not res.stdout.strip():
            return None, None
        try:
            parts = res.stdout.strip().split()
            return int(parts[0]), int(parts[1])
        except Exception:
            return None, None

    async def _resize_or_compress_image(self, path: str, ext: str) -> Tuple[Dict[str, Any], int]:
        q = shlex.quote(path)
        cmd = f"command -v convert >/dev/null 2>&1 && convert {q} -resize {MAX_WIDTH}x{MAX_HEIGHT}\\> -strip -quality 80 jpeg:- | base64 | tr -d '\\n'"
        res = await self._run_cmd(cmd)
        if res.return_code != 0 or not res.stdout.strip():
            return {}, 1
        b64 = res.stdout.strip()
        return self._image_payload(b64, ".jpeg"), 0

    async def _base64_file(self, path: str) -> Tuple[str, int]:
        res = await self._run_cmd(f"base64 {shlex.quote(path)} | tr -d '\\n'")
        if res.return_code == 0 and res.stdout.strip():
            return res.stdout.strip(), 0
        return "", 1

    def _image_payload(self, b64: str, ext: str) -> Dict[str, Any]:
        media_type = f"image/{ext.lstrip('.').lower()}"
        if media_type == "image/jpg":
            media_type = "image/jpeg"
        return {"type": "image", "file": {"base64": b64, "type": media_type}}

    def _append_history(self, instance_id: str, record: Dict[str, Any]) -> None:
        self.execution_history.setdefault(instance_id, []).append(record)

    def _format_file_size_error(self, size_in_bytes: int) -> str:
        return f"File content ({int(math.ceil(size_in_bytes / 1024))}KB) exceeds maximum allowed size ({int(math.ceil(MAX_OUTPUT_SIZE / 1024))}KB). Please provide `offset` and `limit` to read specific portions of the file, or use a search tool to locate specific content."

    def _parse_int(self, s: str) -> Optional[int]:
        if not isinstance(s, str):
            return None
        txt = s.strip()
        m = re.search(r"(\d+)", txt)
        return int(m.group(1)) if m else None
