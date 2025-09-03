import asyncio
import base64
import hashlib
import importlib
import inspect
import json
import os
import random
import re
import shlex
import string
import subprocess
import sys
import time
from dataclasses import is_dataclass, asdict
from typing import Any, Dict, List, Tuple, Optional

import pytest

# -------------------------
# Determinism & Globals
# -------------------------
random.seed(1337)
os.environ.setdefault("PYTHONHASHSEED", "1337")

RESULTS: List[Dict[str, Any]] = []  # Will be emitted at session finish as one JSON line

# -------------------------
# TSX inputSchema analysis (done at generation-time by the model)
# Tool: ReadNotebook
# Zod schema (strictObject):
#   notebook_path: string
# Description: "The absolute path to the Jupyter notebook file to read (must be absolute, not relative)"
#
# Additional validation/behavior inferred from sources:
# - TSX "validateInput" allows relative by resolving cwd, but Python tool enforces:
#     - must start with "/" (absolute) AND end with ".ipynb"
#     - non-existing file -> "File does not exist."
#     - invalid JSON -> "Notebook is not valid JSON."
# - Returns: list of processed notebook "cells"; each cell contains keys like:
#   cell (index), cellType, source, language, execution_count, outputs?
# -------------------------

# Embedded test cases (static; no runtime schema parsing). Keys are snake_case.
TEST_CASES: List[Dict[str, Any]] = [
    # ----------------- Happy Path (>=3) -----------------
    {
        "case_name": "happy_valid_notebook_python_lang",
        "category": "Happy Path",
        "parameters": {"notebook_path": "<<GOOD_NB_1>>"},
    },
    {
        "case_name": "happy_valid_notebook_non_python_lang",
        "category": "Happy Path",
        "parameters": {"notebook_path": "<<GOOD_NB_2_JS_LANG>>"},
    },
    {
        # Relies on defaulting: language_info missing -> tool should default language to "python"
        "case_name": "happy_valid_notebook_default_language",
        "category": "Happy Path",
        "parameters": {"notebook_path": "<<GOOD_NB_3_DEFAULT_LANG>>"},
    },

    # ----------------- Sad Path (>=5) -----------------
    {
        "case_name": "sad_missing_required_parameter",
        "category": "Sad Path",
        "parameters": {},
    },
    {
        "case_name": "sad_wrong_type_for_notebook_path",
        "category": "Sad Path",
        "parameters": {"notebook_path": 123},  # not a string
    },
    {
        "case_name": "sad_relative_path_disallowed",
        "category": "Sad Path",
        "parameters": {"notebook_path": "relative.ipynb"},  # not starting with "/"
    },
    {
        "case_name": "sad_wrong_extension_txt",
        "category": "Sad Path",
        "parameters": {"notebook_path": "<<WRONG_EXT_TXT>>"},  # ends with .txt
    },
    {
        "case_name": "sad_not_found_absolute_ipynb",
        "category": "Sad Path",
        "parameters": {"notebook_path": "/tmp/does_not_exist_9c8f0b2a.ipynb"},
    },
    {
        "case_name": "sad_invalid_json_in_ipynb",
        "category": "Sad Path",
        "parameters": {"notebook_path": "<<BAD_JSON_NB>>"},
    },

    # ----------------- Security (>=2) -----------------
    {
        "case_name": "security_command_injection_like_suffix",
        "category": "Security",
        "parameters": {"notebook_path": "/tmp/evil_file.ipynb; rm -rf /"},
    },
    {
        "case_name": "security_parent_dir_traversal",
        "category": "Security",
        "parameters": {"notebook_path": "/tmp/../../etc/passwd.ipynb"},
    },
]

# Build pytest params with category marks
_CATEGORY_TO_MARK = {
    "Happy Path": pytest.mark.happy,
    "Sad Path": pytest.mark.sad,
    "Security": pytest.mark.security,
}
PARAMS = [
    pytest.param(case, id=case["case_name"], marks=_CATEGORY_TO_MARK.get(case["category"], ()))
    for case in TEST_CASES
]

# -------------------------
# Utility: stable hashing & snake_case
# -------------------------
def _stable_hash(s: str, n: int = 10) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]

_SALT = _stable_hash("readnotebook-pytest-salt", 8)

def _snake_key(k: str) -> str:
    # Convert camelCase / kebab-case to snake_case
    k = k.replace("-", "_")
    k = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", k)
    return k.lower()

def _snake_obj(x: Any) -> Any:
    if isinstance(x, dict):
        return { _snake_key(k): _snake_obj(v) for k, v in x.items() }
    if isinstance(x, list):
        return [_snake_obj(v) for v in x]
    return x

def _short_text_repr(text: str) -> Any:
    if not isinstance(text, str):
        text = str(text)
    if len(text) > 64:
        return {"text_prefix": text[:64]}
    return text

def _normalize_images_in_outputs(obj: Any) -> Any:
    # Replace image base64 with length and hash
    if isinstance(obj, dict):
        new_d = {}
        for k, v in obj.items():
            if k in {"image", "image_data"}:
                if isinstance(v, dict) and "image_data" in v:
                    b64 = v.get("image_data") or ""
                    if not isinstance(b64, str):
                        b64 = str(b64)
                    new_d[k] = {
                        "base64_len": len(b64),
                        "sha256": hashlib.sha256(b64.encode("utf-8")).hexdigest(),
                        **({"media_type": v.get("media_type")} if isinstance(v.get("media_type"), str) else {}),
                    }
                elif isinstance(v, str):
                    new_d[k] = {
                        "base64_len": len(v),
                        "sha256": hashlib.sha256(v.encode("utf-8")).hexdigest(),
                    }
                else:
                    new_d[k] = v
            elif isinstance(v, str) and k in {"text"}:
                new_d[k] = _short_text_repr(v)
            else:
                new_d[k] = _normalize_images_in_outputs(v)
        return new_d
    elif isinstance(obj, list):
        return [_normalize_images_in_outputs(v) for v in obj]
    else:
        return obj

def _canonicalize_result(raw_result: Any) -> Dict[str, Any]:
    # Tool returns a list of cells; normalize into {"cells": [...]}
    data = raw_result
    if not isinstance(data, dict):
        data = {"cells": data}
    data = _snake_obj(data)
    data = _normalize_images_in_outputs(data)
    return data

def _map_error_code(msg: str) -> str:
    m = (msg or "").strip()
    if not m:
        return "RUNTIME_ERROR"
    if m.startswith("Invalid input") or "must be a string" in m or "File must be a Jupyter notebook" in m:
        return "VALIDATION_ERROR"
    if "does not exist" in m:
        return "NOT_FOUND"
    if "Timeout" in m or "failed with rc" in m:
        return "RUNTIME_ERROR"
    if "not valid JSON" in m:
        return "VALIDATION_ERROR"
    return "RUNTIME_ERROR"

def _obj_to_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj):
        return asdict(obj)
    # Try common patterns
    if hasattr(obj, "dict"):
        try:
            return obj.dict()  # type: ignore[attr-defined]
        except Exception:
            pass
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()  # type: ignore[attr-defined]
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    # Last resort: shallow introspection
    d = {}
    for name in ("success", "result", "error", "metrics"):
        if hasattr(obj, name):
            d[name] = getattr(obj, name)
    return d

def _get_field(d: Dict[str, Any], key: str, default: Any = None) -> Any:
    if key in d:
        return d[key]
    # Also check snake/camel variants
    alt = _snake_key(key)
    for k in d.keys():
        if _snake_key(k) == alt:
            return d[k]
    return default

# -------------------------
# Environment & Module Fixtures
# -------------------------
@pytest.fixture(scope="session")
def k8s_env() -> Dict[str, Optional[str]]:
    ns = os.environ.get("K8S_NAMESPACE")
    pod = os.environ.get("K8S_POD_NAME")
    if not ns or not pod:
        pytest.skip("K8S_NAMESPACE and K8S_POD_NAME env vars are required for these tests.")
    return {
        "namespace": ns,
        "pod_name": pod,
        "container": os.environ.get("K8S_CONTAINER"),
        "timeout": os.environ.get("K8S_TIMEOUT", "45"),
    }

@pytest.fixture(scope="session")
def tool_module():
    mod_path = os.environ.get("PY_TOOL_MODULE", "tool_under_test")
    try:
        return importlib.import_module(mod_path)
    except Exception as e:
        pytest.skip(f"Unable to import PY_TOOL_MODULE '{mod_path}': {e}")

@pytest.fixture(scope="session")
def tool_entry(tool_module):
    # Resolve class/object with callable execute_tool
    explicit = os.environ.get("PY_TOOL_SYMBOL")
    if explicit:
        sym = getattr(tool_module, explicit, None)
        if sym is None:
            pytest.skip(f"PY_TOOL_SYMBOL '{explicit}' not found in module.")
        # Ensure has execute_tool
        candidate = sym if not inspect.isclass(sym) else sym
        if not hasattr(candidate, "execute_tool"):
            pytest.skip("Resolved symbol does not expose 'execute_tool'.")
        return candidate

    # Discovery: find first attr with callable execute_tool
    for name in dir(tool_module):
        obj = getattr(tool_module, name)
        target = obj if not inspect.isclass(obj) else obj
        if hasattr(target, "execute_tool"):
            return target
    pytest.skip("No class/object with 'execute_tool' found in module.")

@pytest.fixture(scope="session")
def tool_instance(tool_entry, k8s_env):
    # If class, instantiate; else it's a singleton object
    if inspect.isclass(tool_entry):
        # Prefer single "config" argument if present
        kwargs = {}
        sig = inspect.signature(tool_entry.__init__)
        if len(sig.parameters) >= 2:
            # Many tools take a single "config" dict; respect that if present
            if "config" in sig.parameters:
                cfg = {
                    "namespace": k8s_env["namespace"],
                    "pod_name": k8s_env["pod_name"],
                    "container": k8s_env["container"],
                    "timeout": float(k8s_env["timeout"] or "45"),
                }
                return tool_entry(config=cfg)
        # Fall back to no-arg constructor
        return tool_entry()
    else:
        return tool_entry  # already an instance

@pytest.fixture(scope="session")
def k8s_exec(tool_instance, k8s_env):
    """
    Execute a shell command inside the target pod.
    Prefer the tool's own in-pod exec if exposed; fallback to 'kubectl exec'.
    Returns (stdout, rc).
    """
    async def _run_with_tool(user_cmd: str) -> Tuple[str, int]:
        # Try private async method _run_in_pod(cmd) -> (stdout, rc)
        if hasattr(tool_instance, "_run_in_pod") and inspect.iscoroutinefunction(getattr(tool_instance, "_run_in_pod")):
            return await getattr(tool_instance, "_run_in_pod")(user_cmd)
        # Try using tool's Kubernetes manager if available: _mgr().execute_command(pod, "sh -lc <cmd>")
        if hasattr(tool_instance, "_mgr"):
            try:
                mgr = tool_instance._mgr()  # type: ignore[attr-defined]
                shell_cmd = f"sh -lc {shlex.quote(user_cmd)}"
                # The manager may be sync; run in thread to avoid blocking
                def _call():
                    return mgr.execute_command(k8s_env["pod_name"], shell_cmd)
                stdout, rc = await asyncio.to_thread(_call)
                return str(stdout), int(rc)
            except Exception:
                pass
        raise RuntimeError("Tool does not expose in-pod exec; will fallback to kubectl.")

    def _run_kubectl(user_cmd: str) -> Tuple[str, int]:
        base = ["kubectl", "-n", k8s_env["namespace"], "exec", k8s_env["pod_name"]]
        if k8s_env.get("container"):
            base += ["-c", k8s_env["container"]]
        base += ["--", "/bin/sh", "-lc", user_cmd]
        proc = subprocess.run(base, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return proc.stdout, int(proc.returncode)

    def _exec(user_cmd: str) -> Tuple[str, int]:
        # Soft timeout using time limit; delegate to async path first
        try:
            return asyncio.run(asyncio.wait_for(_run_with_tool(user_cmd), timeout=float(k8s_env["timeout"] or "45")))
        except Exception:
            return _run_kubectl(user_cmd)

    return _exec

# -------------------------
# Pod Preparation Fixture
# -------------------------
@pytest.fixture(scope="session")
def prepared_paths(k8s_exec) -> Dict[str, str]:
    """
    Create a small set of files inside the pod under /tmp for test coverage:
    - 3 valid .ipynb notebooks (python lang, js lang, missing language_info)
    - 1 invalid JSON .ipynb
    - 1 wrong extension .txt
    Returns dict of absolute paths.
    """
    def mk_nb(cells: List[Dict[str, Any]], language_name: Optional[str]) -> str:
        nb: Dict[str, Any] = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": cells,
        }
        if language_name is not None:
            nb["metadata"] = {"language_info": {"name": language_name}}
        return json.dumps(nb, ensure_ascii=False)

    # Minimal cells
    cells_py = [
        {"cell_type": "markdown", "metadata": {}, "source": "Hello notebook"},
        {"cell_type": "code", "metadata": {}, "execution_count": 1, "source": "print('hi')",
         "outputs": [{"output_type": "stream", "name": "stdout", "text": "hi\n"}]},
    ]
    cells_js = [
        {"cell_type": "code", "metadata": {}, "execution_count": 1, "source": "console.log(42);", "outputs": []},
    ]
    cells_default = [
        {"cell_type": "code", "metadata": {}, "execution_count": 1, "source": "a=1\nb=2\na+b", "outputs": []},
    ]

    # Paths (deterministic)
    good1 = f"/tmp/nb_py_{_SALT}.ipynb"
    good2 = f"/tmp/nb_js_{_SALT}.ipynb"
    good3 = f"/tmp/nb_default_{_SALT}.ipynb"
    bad_json = f"/tmp/nb_badjson_{_SALT}.ipynb"
    wrong_ext = f"/tmp/nb_wrong_{_SALT}.txt"

    # Create contents
    payloads = {
        good1: mk_nb(cells_py, "python"),
        good2: mk_nb(cells_js, "javascript"),
        good3: mk_nb(cells_default, None),  # missing language_info
        bad_json: "this is not json at all {][}",
        wrong_ext: "notebook but wrong extension",
    }

    for path, content in payloads.items():
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        cmd = f'printf %s {shlex.quote(b64)} | base64 -d > {shlex.quote(path)}'
        _, rc = k8s_exec(cmd)
        assert rc == 0, f"Failed to write file in pod: {path}"

    # Sanity check files exist
    check_cmd = " && ".join([f'test -f {shlex.quote(p)}' for p in payloads.keys()])
    _, rc = k8s_exec(check_cmd)
    assert rc == 0, "One or more prepared files missing in pod."

    return {
        "GOOD_NB_1": good1,
        "GOOD_NB_2_JS_LANG": good2,
        "GOOD_NB_3_DEFAULT_LANG": good3,
        "BAD_JSON_NB": bad_json,
        "WRONG_EXT_TXT": wrong_ext,
    }

# -------------------------
# Helper: resolve placeholders in case parameters
# -------------------------
def _resolve_params(params: Dict[str, Any], prepared: Dict[str, str]) -> Dict[str, Any]:
    def _resolve_value(v: Any) -> Any:
        if isinstance(v, str) and v.startswith("<<") and v.endswith(">>"):
            key = v.strip("<>").strip()
            return prepared.get(key, v)
        return v
    return {k: _resolve_value(v) for k, v in params.items()}

# -------------------------
# Tool Invocation Helper
# -------------------------
async def _call_tool_async(tool_instance: Any, instance_id: str, params: Dict[str, Any], timeout: float = 45.0) -> Any:
    exec_fn = getattr(tool_instance, "execute_tool", None)
    if exec_fn is None:
        raise RuntimeError("Tool has no 'execute_tool'.")

    sig = inspect.signature(exec_fn)
    kw = {}
    res = None

    # Try to initialize instance lifecycle if available
    if hasattr(tool_instance, "_initialize_instance"):
        init_fn = getattr(tool_instance, "_initialize_instance")
        if inspect.iscoroutinefunction(init_fn):
            try:
                await init_fn(instance_id)
            except Exception:
                pass

    try:
        if len(sig.parameters) >= 2:
            # Likely (instance_id, parameters, **kwargs)
            coro = exec_fn(instance_id, params, **kw)
        else:
            # Likely (parameters, **kwargs)
            coro = exec_fn(params, **kw)

        if inspect.iscoroutine(coro):
            res = await asyncio.wait_for(coro, timeout=timeout)
        else:
            res = coro
    finally:
        if hasattr(tool_instance, "_cleanup_instance"):
            cln_fn = getattr(tool_instance, "_cleanup_instance")
            if inspect.iscoroutinefunction(cln_fn):
                try:
                    await cln_fn(instance_id)
                except Exception:
                    pass

    return res

def _call_tool(tool_instance: Any, case_name: str, params: Dict[str, Any], timeout: float = 45.0) -> Dict[str, Any]:
    instance_id = f"pytest-{_stable_hash(case_name + _SALT, 12)}"
    try:
        result_obj = asyncio.run(_call_tool_async(tool_instance, instance_id, params, timeout=timeout))
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {str(e)}", "result": None, "metrics": {}}
    return _obj_to_dict(result_obj)

# -------------------------
# Physical verification for Happy Path
# -------------------------
def _assert_pod_file_and_cells(k8s_exec, nb_path: str, tool_cells: List[Dict[str, Any]]):
    # 1) File exists
    out, rc = k8s_exec(f'test -f {shlex.quote(nb_path)} && echo OK || echo NO')
    assert "OK" in out and rc == 0, f"Notebook not found in pod: {nb_path}"

    # 2) Read and parse JSON in test, compare number of cells
    out, rc = k8s_exec(f'cat {shlex.quote(nb_path)}')
    assert rc == 0 and out, f"Failed to read notebook content for verification: {nb_path}"
    nb = json.loads(out)
    expected_cells = nb.get("cells", [])
    assert isinstance(expected_cells, list), "Notebook cells is not a list"
    assert len(expected_cells) == len(tool_cells), f"Cell count mismatch: pod={len(expected_cells)} tool={len(tool_cells)}"

# -------------------------
# The Test
# -------------------------
@pytest.mark.parametrize("case", PARAMS, ids=[c["case_name"] for c in TEST_CASES])
def test_k8s_read_notebook(case, tool_instance, prepared_paths, k8s_exec, request):
    # Dynamically mark the test by category for discovery
    cat = case.get("category")
    if cat == "Happy Path":
        request.node.add_marker("happy")
    elif cat == "Sad Path":
        request.node.add_marker("sad")
    elif cat == "Security":
        request.node.add_marker("security")

    # Resolve placeholders to concrete absolute paths prepared in the pod
    params = _resolve_params(case.get("parameters", {}), prepared_paths)

    # Call the imported tool (no wrapping, no re-implementation)
    raw = _call_tool(tool_instance, case["case_name"], params, timeout=60.0)

    success = bool(_get_field(raw, "success", False))
    raw_error = _get_field(raw, "error")
    raw_result = _get_field(raw, "result")

    # Normalize outputs for summary
    summary_item: Dict[str, Any] = {
        "case_name": case["case_name"],
        "success": success,
        "result": None,
        "error": None,
    }

    if success:
        # Canonicalize result
        canon = _canonicalize_result(raw_result)
        summary_item["result"] = canon

        # Assertions for Happy Path
        if cat == "Happy Path":
            assert isinstance(canon.get("cells"), list) and len(canon["cells"]) >= 0, "Result should contain 'cells' list"
            # Physical verification inside the pod
            nb_path = params.get("notebook_path")
            assert isinstance(nb_path, str) and nb_path.startswith("/"), "Happy path must use absolute path"
            _assert_pod_file_and_cells(k8s_exec, nb_path, canon["cells"])

            # Extra checks based on which prepared notebook we used
            if nb_path == prepared_paths["GOOD_NB_2_JS_LANG"]:
                # Language should be "javascript"
                langs = {c.get("language") for c in canon["cells"] if isinstance(c, dict)}
                assert "javascript" in langs, f"Expected language 'javascript' in cells, got {langs}"
            if nb_path == prepared_paths["GOOD_NB_3_DEFAULT_LANG"]:
                # language_info missing -> default "python"
                langs = {c.get("language") for c in canon["cells"] if isinstance(c, dict)}
                assert "python" in langs, f"Expected default language 'python' in cells, got {langs}"

    else:
        # Error normalization
        err_msg = raw_error if isinstance(raw_error, str) else (raw_error.get("message") if isinstance(raw_error, dict) else str(raw_error))
        code = _map_error_code(err_msg or "")
        summary_item["error"] = {"code": code, "message": (err_msg or "")}

        # Assertions for Sad/Security
        assert not success, "Expected failure"
        assert err_msg, "Expected non-empty error message"
        assert code in {"VALIDATION_ERROR", "NOT_FOUND", "SECURITY_BLOCKED", "TOO_LARGE", "RUNTIME_ERROR"}

        # Targeted expectations for some sad/security cases
        if case["case_name"] == "sad_missing_required_parameter":
            assert code == "VALIDATION_ERROR"
        if case["case_name"] == "sad_wrong_type_for_notebook_path":
            assert code == "VALIDATION_ERROR"
        if case["case_name"] == "sad_relative_path_disallowed":
            assert code == "VALIDATION_ERROR"
        if case["case_name"] == "sad_wrong_extension_txt":
            assert code == "VALIDATION_ERROR"
        if case["case_name"] == "sad_not_found_absolute_ipynb":
            assert code == "NOT_FOUND"
        if case["case_name"] == "sad_invalid_json_in_ipynb":
            assert code == "VALIDATION_ERROR"
        if case["case_name"] == "security_parent_dir_traversal":
            assert code in {"NOT_FOUND", "VALIDATION_ERROR"}
        if case["case_name"] == "security_command_injection_like_suffix":
            assert code == "VALIDATION_ERROR"

    RESULTS.append(summary_item)

# -------------------------
# One-line JSON summary emission
# -------------------------
def pytest_sessionfinish(session, exitstatus):
    try:
        payload = json.dumps(RESULTS, ensure_ascii=False)
        out_path = os.environ.get("RESULT_PATH")
        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(payload)
        else:
            # Print exactly one JSON line
            print(payload)
    except Exception:
        # Ensure we still print something minimally valid if unexpected error occurs
        try:
            print(json.dumps([], ensure_ascii=False))
        except Exception:
            # If even that fails, silently swallow to avoid extra output lines
            pass
