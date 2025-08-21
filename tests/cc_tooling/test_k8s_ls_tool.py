import asyncio
import hashlib
import importlib
import inspect
import json
import os
import random
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pytest

# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
random.seed(1337)
os.environ.setdefault("PYTHONHASHSEED", "1337")

# ---------------------------------------------------------------------------
# TSX analysis (performed at generation time) → Embedded tests
#
# The TSX inputSchema is a strict object with a single required field:
#   path: string  — "The absolute path to the directory to list (must be absolute, not relative)"
#
# Behavior (summarized for expectations):
# - Lists directory tree recursively; skips dot-prefixed paths and __pycache__.
# - Prints a tree with "- {abs_root}/" followed by "  - child" lines (and deeper indents).
# - If more than 1000 items, prepends a truncation message but still succeeds.
# - Python tool enforces: path present, string, absolute; returns error for files / nonexistent dirs.
# ---------------------------------------------------------------------------

TEST_CASES: List[Dict[str, Any]] = [
    # Happy Path (3+)
    {
        "case_name": "hp_root_listing",
        "category": "Happy Path",
        "parameters": {"path": "/"},
    },
    {
        "case_name": "hp_tmp_root_listing",
        "category": "Happy Path",
        "parameters": {"path": "/tmp"},
    },
    {
        "case_name": "hp_etc_listing",
        "category": "Happy Path",
        "parameters": {"path": "/etc"},
    },

    # Sad Path (5+)
    {
        "case_name": "sp_missing_required_path",
        "category": "Sad Path",
        "parameters": {},  # missing required 'path'
    },
    {
        "case_name": "sp_path_wrong_type_int",
        "category": "Sad Path",
        "parameters": {"path": 123},  # wrong type
    },
    {
        "case_name": "sp_relative_path_rejected",
        "category": "Sad Path",
        "parameters": {"path": "var/log"},  # not absolute
    },
    {
        "case_name": "sp_empty_string_path",
        "category": "Sad Path",
        "parameters": {"path": ""},  # empty string, not absolute
    },
    {
        "case_name": "sp_not_a_directory",
        "category": "Sad Path",
        "parameters": {"path": "/etc/passwd"},  # file, not dir
    },
    {
        "case_name": "sp_nonexistent_directory",
        "category": "Sad Path",
        "parameters": {"path": "/definitely-not-a-dir-xyz"},
    },

    # Security (2+)
    {
        "case_name": "sec_command_injection_semicolon",
        "category": "Security",
        "parameters": {"path": "/tmp; rm -rf /"},
    },
    {
        "case_name": "sec_path_traversal_collapse",
        "category": "Security",
        "parameters": {"path": "/tmp/../../does-not-exist"},
    },
]

# Build param IDs and marks up front
def _marks_for(cat: str) -> Iterable[pytest.MarkDecorator]:
    if cat.lower().startswith("happy"):
        return (pytest.mark.happy,)
    if cat.lower().startswith("sad"):
        return (pytest.mark.sad,)
    if cat.lower().startswith("security"):
        return (pytest.mark.security,)
    return ()

PARAMS = [
    pytest.param(case, id=case["case_name"], marks=list(_marks_for(case["category"])))
    for case in TEST_CASES
]

# ---------------------------------------------------------------------------
# Register custom markers to silence warnings
# ---------------------------------------------------------------------------
def pytest_configure(config):
    for name, desc in [
        ("happy", "Happy-path cases for LS tool"),
        ("sad", "Sad-path (validation/format) cases for LS tool"),
        ("security", "Security-oriented inputs (treated as data only) for LS tool"),
    ]:
        try:
            config.addinivalue_line("markers", f"{name}: {desc}")
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Global results store (for single-line summary at session end)
# ---------------------------------------------------------------------------
RESULTS: List[Dict[str, Any]] = []

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]

def _get_env_required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        pytest.skip(f"Required environment variable {name} is not set")
    return val

def _to_bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.strip().lower() in {"1", "true", "yes", "on"}
    return bool(x)

def _as_dict_like(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    out: Dict[str, Any] = {}
    for k in ("success", "result", "metrics", "error"):
        out[k] = getattr(obj, k, None)
    return out

def _first64(s: str) -> str:
    return s[:64] if len(s) > 64 else s

def _canonicalize_error(err_text: Optional[str], metrics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Map tool errors to stable {code, message}."""
    msg = (err_text or "").strip()
    rc = None
    if isinstance(metrics, dict):
        rc = metrics.get("rc")
        try:
            rc = int(rc) if rc is not None else None
        except Exception:
            rc = None

    # Schema / validation clues
    if "Invalid input" in msg or "must be absolute" in msg or "must be a string" in msg:
        code = "VALIDATION_ERROR"
    # Timeouts
    elif "Timeout" in msg or rc == 124:
        code = "RUNTIME_ERROR"
    # Kubernetes exec failed with rc==2 (e.g., not found / not a dir)
    elif rc == 2 or "Not a directory" in msg:
        code = "NOT_FOUND"
    else:
        # Default catch-all for unexpected failures
        code = "RUNTIME_ERROR"

    # Short, stable prefix
    stable_message = msg.splitlines()[0][:200] if msg else ""
    return {"code": code, "message": stable_message}

def _extract_top_level_from_tree(tree_text: str) -> List[str]:
    """
    From the rendered tree, capture only first-level children lines.
    Lines look like:
      - <name>[/]
    """
    top: List[str] = []
    for line in tree_text.splitlines():
        if line.startswith("  - "):  # exactly two spaces then dash
            name = line[4:].strip()
            if name:
                top.append(name)
        # deeper levels have more spaces; ignore
    return top

def _filter_hidden(names: Iterable[str]) -> List[str]:
    out: List[str] = []
    for n in names:
        base = n[:-1] if n.endswith("/") else n
        if base.startswith("."):
            continue
        # also skip __pycache__
        if base == "__pycache__":
            continue
        out.append(n)
    return out

def _run_subprocess(cmd: List[str], timeout: int = 30) -> Tuple[str, int, str]:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, text=True)
    return proc.stdout, proc.returncode, proc.stderr

def _async_run(coro):
    return asyncio.run(coro)

# ---------------------------------------------------------------------------
# Pytool discovery & construction
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def tool_module():
    module_name = os.environ.get("PY_TOOL_MODULE", "tool_under_test")
    try:
        return importlib.import_module(module_name)
    except Exception as e:
        pytest.skip(f"Failed to import module {module_name}: {e}")

@pytest.fixture(scope="session")
def tool_entry(tool_module):
    symbol = os.environ.get("PY_TOOL_SYMBOL")
    cand = None
    if symbol:
        if not hasattr(tool_module, symbol):
            pytest.skip(f"PY_TOOL_SYMBOL={symbol} not found in module")
        cand = getattr(tool_module, symbol)
    else:
        # Discover: class or object with a callable 'execute_tool'
        for name in dir(tool_module):
            attr = getattr(tool_module, name)
            exec_attr = getattr(attr, "execute_tool", None)
            if callable(exec_attr):
                cand = attr
                break
        if cand is None:
            pytest.skip("No suitable tool export found (object/class with callable 'execute_tool')")
    return cand

@pytest.fixture(scope="session")
def tool_ctor_and_is_class(tool_entry):
    if inspect.isclass(tool_entry):
        return tool_entry, True
    return tool_entry, False

@pytest.fixture(scope="session")
def k8s_env():
    ns = _get_env_required("K8S_NAMESPACE")
    pod = _get_env_required("K8S_POD_NAME")
    container = os.environ.get("K8S_CONTAINER")
    timeout_s = int(os.environ.get("TOOL_TIMEOUT", "30"))
    kubeconfig = os.environ.get("K8S_KUBECONFIG_PATH") or os.environ.get("KUBECONFIG")
    return {"namespace": ns, "pod_name": pod, "container": container, "timeout": timeout_s, "kubeconfig_path": kubeconfig}

@pytest.fixture(scope="session")
def tool_instance(tool_ctor_and_is_class, k8s_env):
    ctor, is_class = tool_ctor_and_is_class
    if is_class:
        # Try constructing with common kwargs; ignore unexpected kwargs.
        kwargs = {}
        for k in ("namespace", "pod_name", "container", "timeout", "kubeconfig_path"):
            if k in k8s_env and k8s_env[k]:
                kwargs[k] = k8s_env[k]
        try:
            return ctor(kwargs)  # many tools accept a single config dict
        except TypeError:
            # Fallback: try kwargs style
            try:
                return ctor(**kwargs)
            except Exception as e:
                pytest.skip(f"Failed to construct tool: {e}")
    else:
        # Already an instance/object
        return ctor

@pytest.fixture(scope="session")
def k8s_exec(tool_instance, k8s_env):
    """
    Returns a callable: (cmd: str, timeout: int=30) -> (stdout: str, rc: int)
    Prefers the tool's own in-pod execution if exposed; else falls back to kubectl exec.
    """
    run_in_pod = getattr(tool_instance, "_run_in_pod", None)

    if callable(run_in_pod):
        def _call(cmd: str, timeout: int = 30) -> Tuple[str, int]:
            # Tool's _run_in_pod wraps with 'sh -lc'
            try:
                out, rc = _async_run(run_in_pod(cmd))
                try:
                    rc = int(rc)
                except Exception:
                    rc = -1
                return str(out), rc
            except Exception as e:
                return f"exec_failed: {e}", -1
        return _call

    # Fallback to system kubectl (no external Python libs)
    ns = k8s_env["namespace"]
    pod = k8s_env["pod_name"]
    container = k8s_env.get("container")
    def _kubectl(cmd: str, timeout: int = 30) -> Tuple[str, int]:
        base = ["kubectl", "-n", ns, "exec", pod]
        if container:
            base.extend(["-c", container])
        base.extend(["--", "/bin/sh", "-lc", cmd])
        try:
            out, rc, err = _run_subprocess(base, timeout=timeout)
            return out, rc
        except subprocess.TimeoutExpired:
            return "timeout", 124
        except Exception as e:
            return f"kubectl_failed: {e}", -1
    return _kubectl

# ---------------------------------------------------------------------------
# Tool invocation helper (supports different execute_tool signatures)
# ---------------------------------------------------------------------------

def _sig_accepts_instance_id(fn: Callable) -> bool:
    try:
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        # For bound method, 'self' is already bound, so param[0] may be 'instance_id'
        return len(params) >= 2 or (len(params) == 1 and params[0].name != "parameters")
    except Exception:
        return True  # default to True for safety

def _invoke_tool(tool_instance, instance_id: str, parameters: Dict[str, Any], timeout_s: int = 30):
    async def _run():
        # Initialize if supported
        init = getattr(tool_instance, "_initialize_instance", None)
        if init and inspect.iscoroutinefunction(init):
            try:
                await init(instance_id)
            except Exception:
                pass

        exec_fn = getattr(tool_instance, "execute_tool")
        result = None
        if inspect.iscoroutinefunction(exec_fn):
            try:
                if _sig_accepts_instance_id(exec_fn):
                    result = await asyncio.wait_for(exec_fn(instance_id, parameters), timeout=timeout_s + 5)
                else:
                    result = await asyncio.wait_for(exec_fn(parameters), timeout=timeout_s + 5)
            except Exception as e:
                result = {"success": False, "error": f"RUNTIME: {type(e).__name__}: {e}", "metrics": {"rc": -1}}
        else:
            try:
                if _sig_accepts_instance_id(exec_fn):
                    result = exec_fn(instance_id, parameters)
                else:
                    result = exec_fn(parameters)
            except Exception as e:
                result = {"success": False, "error": f"RUNTIME: {type(e).__name__}: {e}", "metrics": {"rc": -1}}

        # Cleanup if supported
        cleanup = getattr(tool_instance, "_cleanup_instance", None)
        if cleanup and inspect.iscoroutinefunction(cleanup):
            try:
                await cleanup(instance_id)
            except Exception:
                pass
        return result

    return _async_run(_run())

# ---------------------------------------------------------------------------
# Core test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", PARAMS)
def test_k8s_ls_tool(case: Dict[str, Any], tool_instance, k8s_exec, k8s_env):
    case_name: str = case["case_name"]
    category: str = case["category"]
    params: Dict[str, Any] = case["parameters"]

    instance_id = f"it-{_stable_hash(case_name)}"
    timeout_s = int(k8s_env.get("timeout", 30)) if isinstance(k8s_env, dict) else 30

    # Run the tool
    res_raw = _invoke_tool(tool_instance, instance_id, params, timeout_s=timeout_s)

    res = _as_dict_like(res_raw)
    success = _to_bool(res.get("success"))
    metrics = res.get("metrics") if isinstance(res.get("metrics"), dict) else {}
    err_text = res.get("error") if isinstance(res.get("error"), str) else (res.get("error", {}).get("message") if isinstance(res.get("error"), dict) else None)
    result_text = res.get("result") if isinstance(res.get("result"), str) else (json.dumps(res.get("result")) if res.get("result") is not None else "")

    # Canonicalize output
    normalized_result: Optional[Dict[str, Any]] = None
    normalized_error: Optional[Dict[str, Any]] = None

    if success:
        # Expect a printable tree string even if empty
        text_prefix = _first64(result_text or "")
        top_level = _extract_top_level_from_tree(result_text or "")
        normalized_result = {
            "path": params.get("path"),
            "text_prefix": text_prefix,
            "truncated": bool(metrics.get("truncated", False)),
            "top_level_count": len(top_level),
        }

        # If this is a "Happy Path", attempt physical verification in the pod.
        if category.lower().startswith("happy"):
            path = params.get("path")
            if isinstance(path, str):
                # Build a portable 'ls -1' (avoid -A for busybox); we filter hidden anyway.
                ls_cmd = (
                    f'target={shlex_quote(path)}; '
                    f'if [ ! -d "$target" ]; then echo "NOT_A_DIR"; exit 2; fi; '
                    f'ls -1 "$target" 2>/dev/null | '
                    f'while IFS= read -r n; do '
                    f'if [ -d "$target/$n" ]; then echo "$n/"; else echo "$n"; fi; done'
                )
                pod_out, pod_rc = k8s_exec(ls_cmd, timeout=timeout_s)
                # Don't hard-fail on odd environments; only check consistency when rc==0
                if pod_rc == 0:
                    real_names = [ln.strip() for ln in pod_out.splitlines() if ln.strip()]
                    real_names = _filter_hidden(real_names)
                    for name in top_level:
                        # The tool filters hidden and __pycache__, so compare against filtered set
                        assert name in set(real_names), f"Top-level item mismatch: {name} not in pod ls for {path}"

    else:
        # Failure path — canonicalize error, but do not force category-specific assertions
        normalized_error = _canonicalize_error(err_text, metrics)

    # Store per-case summary (canonicalized)
    RESULTS.append({
        "case_name": case_name,
        "success": bool(success),
        "result": normalized_result if success else None,
        "error": normalized_error if not success else None,
    })

# ---------------------------------------------------------------------------
# Utility: shlex.quote re-implementation using stdlib only (no import implied)
# ---------------------------------------------------------------------------

def shlex_quote(s: str) -> str:
    """Minimal, POSIX-safe quoting (used only for building in-pod shell)."""
    if not s:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"

# ---------------------------------------------------------------------------
# Session finish: emit single-line JSON (or write to RESULT_PATH)
# ---------------------------------------------------------------------------

def pytest_sessionfinish(session, exitstatus):
    try:
        payload = json.dumps(RESULTS, ensure_ascii=False, separators=(",", ":"))
        result_path = os.environ.get("RESULT_PATH")
        if result_path:
            with open(result_path, "w", encoding="utf-8") as f:
                f.write(payload)
        else:
            # Print exactly one JSON line
            print(payload)
    except Exception:
        # Do not raise; best-effort
        pass
