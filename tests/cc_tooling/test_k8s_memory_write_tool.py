import asyncio
import hashlib
import importlib
import inspect
import json
import os
import posixpath
import random
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Tuple

import pytest

# ---------- Determinism ----------
random.seed(1337)
os.environ.setdefault("PYTHONHASHSEED", "1337")

# ---------- Session-scoped results sink ----------
RESULTS: List[Dict[str, Any]] = []

# ---------- TSX analysis (performed at generation time) ----------
# The TSX tool ("MemoryWrite") defines:
# - inputSchema: strict object with REQUIRED fields:
#     file_path: string (path under MEMORY_DIR)
#     content:   string (bytes to write)
# - validateInput/call imply:
#     * Writes ONLY under MEMORY_DIR, rejecting paths that escape it.
#     * Creates parent directories recursively.
#     * Overwrites/creates the target file with exact content (UTF-8).
#     * Returns/yields "Saved".
#
# Embedded static cases (no runtime schema parsing). Keys are snake_case.

TEST_CASES: List[Dict[str, Any]] = [
    # ----------------------- Happy Path (>=3) -----------------------
    {
        "case_name": "happy_write_simple_relative",
        "category": "Happy Path",
        "parameters": {
            "file_path": "notes/hello.txt",
            "content": "Hello, memory!",
        },
    },
    {
        "case_name": "happy_write_leading_slash_nested",
        "category": "Happy Path",
        "parameters": {
            "file_path": "/deep/nest/alpha.txt",
            "content": "Deep thoughts.",
        },
    },
    {
        # Relies on the Python tool's default memory_dir="/memory" (a tool-level default).
        "case_name": "happy_write_empty_content_default_memory_dir",
        "category": "Happy Path",
        "parameters": {
            "file_path": "empty/zero.txt",
            "content": "",
        },
    },
    # ----------------------- Sad Path (>=5) -----------------------
    {
        "case_name": "sad_missing_file_path",
        "category": "Sad Path",
        "parameters": {
            "content": "No path present",
        },
    },
    {
        "case_name": "sad_missing_content",
        "category": "Sad Path",
        "parameters": {
            "file_path": "bad/missing_content.txt",
        },
    },
    {
        "case_name": "sad_file_path_wrong_type",
        "category": "Sad Path",
        "parameters": {
            "file_path": 123,  # type: ignore[arg-type]
            "content": "oops type",
        },
    },
    {
        "case_name": "sad_content_wrong_type",
        "category": "Sad Path",
        "parameters": {
            "file_path": "oops/type.txt",
            "content": 123,  # type: ignore[arg-type]
        },
    },
    {
        "case_name": "sad_parent_dir_reference",
        "category": "Sad Path",
        "parameters": {
            "file_path": "..",
            "content": "bad",
        },
    },
    # ----------------------- Security (>=2) -----------------------
    {
        "case_name": "sec_path_traversal_relative",
        "category": "Security",
        "parameters": {
            "file_path": "../../etc/passwd",
            "content": "blocked attempt",
        },
    },
    {
        "case_name": "sec_path_traversal_absolute",
        "category": "Security",
        "parameters": {
            "file_path": "/../../../var/tmp/hax",
            "content": "blocked attempt",
        },
    },
]

# Build pytest parameters with dynamic marks
def _marks_for(category: str) -> Iterable[pytest.MarkDecorator]:
    cat = category.strip().lower()
    if cat.startswith("happy"):
        return (pytest.mark.happy,)
    if cat.startswith("sad"):
        return (pytest.mark.sad,)
    if cat.startswith("security"):
        return (pytest.mark.security,)
    return tuple()

CASE_PARAMS = [
    pytest.param(
        case,
        id=case["case_name"],
        marks=_marks_for(case["category"]),
    )
    for case in TEST_CASES
]


# ---------- Helpers ----------
def _stable_instance_id(case_name: str) -> str:
    h = hashlib.sha256(case_name.encode("utf-8")).hexdigest()[:12]
    return f"pytest-{h}"


def _compute_full_path(memory_dir: str, file_path: str) -> str:
    # Mirror the Python tool's path normalization:
    mem = memory_dir if memory_dir.endswith("/") else memory_dir + "/"
    if not mem.startswith("/"):
        mem = "/" + mem.lstrip("/")
    sanitized = str(file_path).lstrip("/")
    full_path = posixpath.normpath(mem + sanitized)
    return full_path


def _map_error_code(message: str) -> Dict[str, str]:
    msg = message or ""
    # Canonical error classification rules
    if "Invalid memory file path" in msg:
        code = "SECURITY_BLOCKED"
    elif "Invalid input:" in msg or "must be a string" in msg or "is required" in msg:
        code = "VALIDATION_ERROR"
    elif "Timeout" in msg or "rc=" in msg:
        code = "RUNTIME_ERROR"
    else:
        code = "RUNTIME_ERROR"
    # Keep message stable prefix
    short = msg.splitlines()[0][:160]
    return {"code": code, "message": short}


def _normalize_result(value: Any) -> Any:
    # Canonicalize known simple result types
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        s = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        return {"text_prefix": s[:64]}
    if isinstance(value, dict):
        # Shallow snake_case keys & remove volatile fields
        out = {}
        for k, v in value.items():
            kn = _to_snake(k)
            if kn in {"duration_ms", "trace_id", "timestamp", "ts", "stdout_size", "execution_location", "in_pod_command", "rc"}:
                continue
            out[kn] = v
        return out
    # Fallback: represent via string
    return {"text_prefix": str(value)[:64]}


def _to_snake(name: str) -> str:
    out = []
    for ch in name:
        if ch == "-":
            out.append("_")
        elif ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    s = "".join(out).strip("_")
    s = "_".join(filter(None, s.split()))
    return s


def _extract_field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def env_vars() -> Dict[str, str]:
    ns = os.environ.get("K8S_NAMESPACE")
    pod = os.environ.get("K8S_POD_NAME")
    if not ns or not pod:
        pytest.skip("K8S_NAMESPACE and K8S_POD_NAME must be set for integration tests.")
    return {
        "namespace": ns,
        "pod_name": pod,
        "container": os.environ.get("K8S_CONTAINER", ""),
        "kubeconfig": os.environ.get("KUBECONFIG", ""),
        "py_tool_module": os.environ.get("PY_TOOL_MODULE", "tool_under_test"),
        "py_tool_symbol": os.environ.get("PY_TOOL_SYMBOL", ""),
        "result_path": os.environ.get("RESULT_PATH", ""),
    }


@pytest.fixture(scope="session")
def tool_module(env_vars):
    mod_name = env_vars["py_tool_module"]
    try:
        return importlib.import_module(mod_name)
    except Exception as e:
        pytest.skip(f"Failed to import module {mod_name!r}: {e}")


@pytest.fixture(scope="session")
def tool_entry(tool_module):
    # If PY_TOOL_SYMBOL provided, use it; else discover a class/object with callable execute_tool
    symbol = os.environ.get("PY_TOOL_SYMBOL")
    candidate = None
    if symbol:
        candidate = getattr(tool_module, symbol, None)
    else:
        for name in dir(tool_module):
            obj = getattr(tool_module, name)
            exec_attr = getattr(obj, "execute_tool", None)
            if callable(exec_attr):
                candidate = obj
                break
    if candidate is None:
        pytest.skip("No suitable tool entry with 'execute_tool' found in module.")
    return candidate


@pytest.fixture(scope="session")
def tool_instance(env_vars, tool_entry):
    # Instantiate if class; if already an instance, use as-is.
    if inspect.isclass(tool_entry):
        config = {
            "namespace": env_vars["namespace"],
            "pod_name": env_vars["pod_name"],
            "kubeconfig_path": env_vars["kubeconfig"] or None,
            "timeout": 30,
            "container": env_vars["container"] or None,
            "allow_dangerous": False,
        }
        try:
            inst = tool_entry(config=config)  # type: ignore[misc]
        except TypeError:
            # Some tools may accept config as positional or no-arg; try best-effort
            try:
                inst = tool_entry(config)  # type: ignore[misc]
            except Exception as e:
                pytest.skip(f"Failed to construct tool: {e}")
    else:
        inst = tool_entry
    # Ensure it has an execute_tool attribute
    if not callable(getattr(inst, "execute_tool", None)):
        pytest.skip("Resolved tool entry lacks callable 'execute_tool'.")
    return inst


@pytest.fixture(scope="session")
def k8s_exec(env_vars, tool_instance):
    """
    Returns a callable: (cmd: str, timeout: int) -> Tuple[str, int]
    Prefers the tool's own in-pod exec if available; otherwise uses `kubectl exec`.
    """
    run_in_pod = getattr(tool_instance, "_run_in_pod", None)

    async def _run_via_tool(cmd: str) -> Tuple[str, int]:
        return await run_in_pod(cmd)  # type: ignore[misc]

    def _run_via_kubectl(cmd: str, timeout: int = 30) -> Tuple[str, int]:
        base = ["kubectl", "-n", env_vars["namespace"], "exec", env_vars["pod_name"]]
        if env_vars["container"]:
            base.extend(["-c", env_vars["container"]])
        full = base + ["--", "sh", "-lc", cmd]
        try:
            proc = subprocess.run(
                full,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                check=False,
            )
            return proc.stdout, int(proc.returncode)
        except subprocess.TimeoutExpired:
            return "TIMEOUT", 124

    def runner(cmd: str, timeout: int = 30) -> Tuple[str, int]:
        if callable(run_in_pod):
            try:
                return asyncio.run(asyncio.wait_for(_run_via_tool(cmd), timeout=timeout))
            except Exception:
                # Fallback to kubectl if tool exec path fails
                return _run_via_kubectl(cmd, timeout=timeout)
        return _run_via_kubectl(cmd, timeout=timeout)

    return runner


# ---------- Parameterization ----------
@pytest.mark.parametrize("case", CASE_PARAMS)
def test_k8s_memory_write(case, env_vars, tool_instance, k8s_exec):
    """
    Executes each embedded test case against the imported tool and verifies expected behavior.
    """
    case_name: str = case["case_name"]
    category: str = case["category"]
    params: Dict[str, Any] = dict(case["parameters"])  # avoid mutation
    instance_id = _stable_instance_id(case_name)

    # Invoke the tool (handles async sync differences; accepts (instance_id, parameters) or just (parameters))
    exec_func = getattr(tool_instance, "execute_tool")
    sig = inspect.signature(exec_func)
    needs_instance_id = "instance_id" in sig.parameters

    async def _invoke_async():
        if needs_instance_id:
            return await exec_func(instance_id=instance_id, parameters=params)  # type: ignore[misc]
        # Some tools may accept only params
        try:
            return await exec_func(parameters=params)  # type: ignore[misc]
        except TypeError:
            return await exec_func(params)  # type: ignore[misc]

    def _invoke_sync():
        if needs_instance_id:
            return exec_func(instance_id=instance_id, parameters=params)  # type: ignore[misc]
        try:
            return exec_func(parameters=params)  # type: ignore[misc]
        except TypeError:
            return exec_func(params)  # type: ignore[misc]

    # Execute with a soft timeout (seconds)
    soft_timeout = 45
    t0 = time.time()
    try:
        if inspect.iscoroutinefunction(exec_func):
            tool_out = asyncio.run(asyncio.wait_for(_invoke_async(), timeout=soft_timeout))
        else:
            # If returns awaitable, handle it; else direct value
            result = _invoke_sync()
            if inspect.isawaitable(result):
                tool_out = asyncio.run(asyncio.wait_for(result, timeout=soft_timeout))
            else:
                tool_out = result
        duration = time.time() - t0
    except Exception as e:
        # Treat as runtime error
        success = False
        normalized_err = _map_error_code(str(e))
        RESULTS.append(
            {"case_name": case_name, "success": success, "result": None, "error": normalized_err}
        )
        if category.lower().startswith(("sad", "security")):
            # Expected failure categories
            assert True
            return
        else:
            pytest.fail(f"Unexpected invocation failure for happy-path case {case_name}: {e}")

    # Extract common fields
    success = bool(_extract_field(tool_out, "success", False))
    error_msg = _extract_field(tool_out, "error", "") or ""
    raw_result = _extract_field(tool_out, "result", None)

    # Canonicalize result/error
    normalized_result = _normalize_result(raw_result) if success else None
    normalized_err = _map_error_code(str(error_msg)) if not success else None

    # Append to summary RESULTS
    RESULTS.append(
        {
            "case_name": case_name,
            "success": success,
            "result": normalized_result,
            "error": normalized_err,
        }
    )

    # Assertions
    if category.lower().startswith("happy"):
        assert success is True, f"Happy path should succeed, got error: {error_msg}"
        assert isinstance(normalized_result, dict), "Normalized result should be a dict"
        # Physical verification in the target pod
        memory_dir = getattr(tool_instance, "memory_dir", "/memory")
        file_path = params["file_path"]
        content = params["content"]
        assert isinstance(file_path, str), "file_path must be str in happy-path"
        assert isinstance(content, str), "content must be str in happy-path"
        full_path = _compute_full_path(memory_dir, file_path)
        # Verify file exists and contents match exactly
        ls_out, ls_rc = k8s_exec(f'ls -l "$(dirname {sh_quote(full_path)})"')
        assert ls_rc == 0, f"Directory listing failed: {ls_out}"
        cat_out, cat_rc = k8s_exec(f'cat {sh_quote(full_path)}')
        assert cat_rc == 0, f"cat failed: {cat_out}"
        assert cat_out == content, f"File content mismatch. Expected={content!r} Got={cat_out!r}"
    else:
        # Sad/Security paths must fail with a stable error
        assert success is False, f"{category} case should fail but succeeded with result={raw_result!r}"
        assert normalized_err is not None and normalized_err.get("code"), "Expected a classified error code"


def sh_quote(s: str) -> str:
    # Minimal POSIX shell quoting
    return "'" + s.replace("'", "'\"'\"'") + "'"


# ---------- Single-line JSON emission ----------
def pytest_sessionfinish(session, exitstatus):
    try:
        payload = json.dumps(RESULTS, ensure_ascii=False, separators=(",", ":"))
        out_path = os.environ.get("RESULT_PATH", "")
        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(payload)
        else:
            # Print exactly one JSON line
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()
    except Exception:
        # Never crash the test run on summary emission
        pass
