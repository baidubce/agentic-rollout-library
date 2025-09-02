import os
import re
from typing import Any, Dict, Optional, Tuple

from workers.core.enhanced_base_tool import CCToolBase
from workers.core.tool_schemas import (
    OpenAIFunctionToolSchema,
    ToolResult,
    create_openai_tool_schema,
)


class K8sFileEditTool(CCToolBase):
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        cfg = cfg or {}
        self.namespace: str = cfg.get("namespace", "default")
        self.pod_name: str = cfg.get("pod_name", "target-pod")
        self.base_dir: str = os.path.normpath(cfg.get("base_dir", "/app"))
        from kodo.core import KubernetesManager
        self.k8s = KubernetesManager()
        super().__init__(cfg)

    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        return create_openai_tool_schema(
            name="k8s_file_edit",
            description="Edit files in a Kubernetes pod",
            parameters={
                "file_path": {
                    "type": "string",
                    "description": "The absolute path to the file to modify (or a path under base_dir)",
                },
                "old_string": {
                    "type": "string",
                    "description": "The text to replace (empty means create a new file with new_string)",
                },
                "new_string": {
                    "type": "string",
                    "description": "The text to replace it with (empty means delete matched content)",
                },
            },
            required=["file_path", "old_string", "new_string"],
        )

    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        ok, msg = self._validate_param_shape(parameters)
        if not ok:
            return ToolResult(success=False, error=msg)

        raw_path = str(parameters["file_path"])
        old_str = str(parameters["old_string"])
        new_str = str(parameters["new_string"])

        ok, resolved_or_err = self._resolve_path(raw_path)
        if not ok:
            return ToolResult(success=False, error=resolved_or_err)
        path = resolved_or_err

        if old_str == new_str:
            return ToolResult(success=False, error="No changes to make: old_string and new_string are exactly the same.")

        if old_str == "":
            if self._exists(path):
                return ToolResult(success=False, error="Cannot create new file - file already exists.")
            w_ok, w_err = self._write_file(path, new_str)
            if not w_ok:
                return ToolResult(success=False, error=w_err or "Failed to write file.")
            return ToolResult(success=True, result={"file_path": path, "action": "create"})

        if path.endswith(".ipynb"):
            return ToolResult(success=False, error="File is a Jupyter Notebook. Use the NotebookEditTool to edit this file.")

        r_ok, content = self._read_file(path, allow_missing=True)
        if not r_ok:
            return ToolResult(success=False, error="File does not exist.")

        matches = content.count(old_str)
        if matches == 0:
            return ToolResult(success=False, error="String to replace not found in file.")
        if matches > 1:
            return ToolResult(
                success=False,
                error=f"Found {matches} matches of the string to replace. For safety, this tool only supports replacing exactly one occurrence at a time.",
            )

        updated = content.replace(old_str, new_str, 1)
        w_ok, w_err = self._write_file(path, updated)
        if not w_ok:
            return ToolResult(success=False, error=w_err or "Failed to write file.")
        return ToolResult(success=True, result={"file_path": path, "action": "update" if new_str else "delete"})

    def _validate_param_shape(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        req = ["file_path", "old_string", "new_string"]
        for k in req:
            if k not in params:
                return False, f"Missing required parameter: {k}"
        if not isinstance(params["file_path"], str):
            return False, "Invalid parameter type: file_path must be a string"
        if not isinstance(params["old_string"], str):
            return False, "Invalid parameter type: old_string must be a string"
        if not isinstance(params["new_string"], str):
            return False, "Invalid parameter type: new_string must be a string"
        if params["file_path"] == "":
            return False, "File path must be non-empty"
        return True, ""

    def _resolve_path(self, p: str) -> Tuple[bool, str]:
        if "\x00" in p:
            return False, "Invalid file path: contains null byte"
        if re.search(r"[;&|`$<>]", p):
            return False, "Invalid file path"
        if os.path.isabs(p):
            abs_path = os.path.normpath(p)
        else:
            abs_path = os.path.normpath(os.path.join(self.base_dir, p))
        base = self.base_dir if self.base_dir.endswith(os.sep) else self.base_dir + os.sep
        if not (abs_path == self.base_dir or abs_path.startswith(base)):
            return False, "Invalid file path: escapes base directory"
        return True, abs_path

    def _rc_to_int(self, code: Any) -> int:
        if isinstance(code, int):
            return code
        s = str(code)
        try:
            return int(s)
        except Exception:
            pass
        m = re.search(r"(-?\d+)", s)
        if m:
            return int(m.group(1))
        return 1

    def _sh_double_quote(self, s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _exec(self, cmd: str) -> Tuple[str, int]:
        out, code = self.k8s.execute_command(self.pod_name, cmd)
        out_s = out.decode("utf-8", errors="ignore") if isinstance(out, bytes) else str(out)
        rc = self._rc_to_int(code)
        if rc != 0 and not cmd.lstrip().startswith("/bin/sh") and any(tok in cmd for tok in ("&&", ">", "<<", "\n")):
            try:
                sh_cmd = f"/bin/sh -lc {self._sh_double_quote(cmd)}"
                out2, code2 = self.k8s.execute_command(self.pod_name, sh_cmd)
                out_s2 = out2.decode("utf-8", errors="ignore") if isinstance(out2, bytes) else str(out2)
                rc2 = self._rc_to_int(code2)
                if rc2 == 0:
                    return out_s2, rc2
            except Exception:
                pass
        return out_s, rc

    def _exists(self, path: str) -> bool:
        _, rc = self._exec(f"cat {path}")
        if rc == 0:
            return True
        _, rc2 = self._exec(f"ls {path}")
        return rc2 == 0

    def _read_file(self, path: str, allow_missing: bool = False) -> Tuple[bool, str]:
        out, code = self._exec(f"cat {path}")
        if self._rc_to_int(code) == 0:
            return True, out
        return (False, "") if allow_missing else (False, "")

    def _sh_single_quote(self, s: str) -> str:
        return "'" + s.replace("'", "'\"'\"'") + "'"

    def _write_file(self, path: str, content: str) -> Tuple[bool, str]:
        try:
            dirn = os.path.dirname(path) or "."
            mk_out, mk_code = self._exec(f"mkdir -p {dirn}")
            if self._rc_to_int(mk_code) != 0:
                return False, "Failed to write file."
            q_cnt = self._sh_single_quote(content)
            out, code = self._exec(f"printf %s {q_cnt} > {path}")
            if self._rc_to_int(code) == 0:
                return True, ""
            hd_cmd = f"cat << 'EOF' > {path}\n{content}\nEOF"
            out2, code2 = self._exec(hd_cmd)
            if self._rc_to_int(code2) == 0:
                return True, ""
            return False, "Failed to write file."
        except Exception:
            return False, "Failed to write file."
