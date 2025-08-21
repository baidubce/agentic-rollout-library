# test_k8s_tool_integration.py
import asyncio
import hashlib
import importlib
import inspect
import json
import os
import random
import re
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pytest

# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------
random.seed(1337)
os.environ.setdefault("PYTHONHASHSEED", "1337")

TEST_CASES: List[Dict[str, Any]] = [
    # --------------------------- Happy Path (>=3) ---------------------------
    {
        "case_name": "happy_empty_input",
        "category": "Happy Path",
        "parameters": {},
    },
    {
        "case_name": "happy_arbitrary_fields",
        "category": "Happy Path",
        "parameters": {"foo": "bar", "count": 3, "enabled": True},
    },
    {
        "case_name": "happy_nested_and_array_defaults",
        "category": "Happy Path",
        "parameters": {
            "note": "no required fields; relying on tool defaults",
            "nested": {"a": 1, "b": {"c": [1, 2, 3]}},
            "list": [1, 2, 3],
        },
    },
    # ----------------------------- Sad Path (>=5) ---------------------------
    # These induce failures by altering tool configuration (pod/namespace/timeout),
    # since the schema itself accepts any object.
    {
        "case_name": "sad_pod_not_found",
        "category": "Sad Path",
        "parameters": {"hint": "use a definitely-nonexistent pod name"},
    },
    {
        "case_name": "sad_namespace_not_found",
        "category": "Sad Path",
        "parameters": {"hint": "use a definitely-nonexistent namespace"},
    },
    {
        "case_name": "sad_timeout_tiny",
        "category": "Sad Path",
        "parameters": {"hint": "force extremely small timeout to trigger timeout"},
    },
    {
        "case_name": "sad_timeout_zeroish",
        "category": "Sad Path",
        "parameters": {"hint": "timeout ~0 should also time out"},
    },
    {
        "case_name": "sad_bad_kubeconfig_path",
        "category": "Sad Path",
        "parameters": {"hint": "point to a kubeconfig that does not exist"},
    },
    # ----------------------------- Security (>=2) ---------------------------
    # Inputs include command-injection-like content (ignored by the tool),
    # but we also misconfigure target to ensure failure and verify error mapping.
    {
        "case_name": "sec_injection_like_input_pod_missing",
        "category": "Security",
        "parameters": {"cmd": 'echo "safe"; rm -rf /; :', "path": "../../etc/passwd"},
    },
    {
        "case_name": "sec_path_traversal_like_input_ns_missing",
        "category": "Security",
        "parameters": {"download": "../..//var/log/app.log", "args": ["; cat /etc/shadow"]},
    },
]

# Session-scoped result accumulator for the final one-line JSON.
RESULTS: List[Dict[str, Any]] = []

# --------------------------------------------------------------------------------------
# Utility: Canonicalization & helpers
# --------------------------------------------------------------------------------------
_ALLOWED_ERROR_CODES = {"VALIDATION_ERROR", "NOT_FOUND", "SECURITY_BLOCKED", "TOO_LARGE", "RUNTIME_ERROR"}


def _stable_hex(name: str, n: int = 8) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:n]


def _lower_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {re.sub(r"[-\s]", "_", k).lower(): _lower_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_lower_keys(x) for x in obj]
    return obj


def _classify_error(message: str) -> str:
    m = (message or "").lower()
    if any(x in m for x in ["forbidden", "unauthorized", "permission denied", "rbac"]):
        return "SECURITY_BLOCKED"
    if "not found" in m or "no such pod" in m or "failed to find" in m or "could not find" in m:
        return "NOT_FOUND"
    if "timeout" in m or "timed out" in m:
        return "RUNTIME_ERROR"
    # Tool doesn't validate input shape, so VALIDATION_ERROR is unlikely.
    return "RUNTIME_ERROR"


def _normalize_result(tool_res: Any) -> Tuple[bool, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Return (success, result_dict_or_none, error_dict_or_none) in a stable, comparable shape.
    """
    # Duck-typed access for ToolResult-like objects
    success = getattr(tool_res, "success", None)
    error = getattr(tool_res, "error", None)
    result = getattr(tool_res, "result", None)
    metrics = getattr(tool_res, "metrics", None)

    # Normalize metrics: keep semantic, stable bits only
    norm_metrics = {}
    if isinstance(metrics, dict):
        keep = ["rc", "stdout_size", "in_pod_command", "execution_location"]
        for k in keep:
            if k in metrics:
                norm_metrics[k] = metrics[k]

    # Normalize result content
    norm_result = None
    if result in ("", None, []):
        norm_result = {"content": None}
    elif isinstance(result, (bytes, bytearray)):
        norm_result = {"base64_len": len(result), "sha256": hashlib.sha256(result).hexdigest()}
    elif isinstance(result, str):
        prefix = result[:64]
        norm_result = {"text_prefix": prefix}
    elif isinstance(result, dict):
        norm_result = _lower_keys(result)
    else:
        try:
            norm_result = _lower_keys(result)
        except Exception:
            norm_result = {"text_prefix": str(result)[:64]}

    if norm_metrics:
        norm_result = {"metrics": norm_metrics, **(norm_result or {})}

    # Normalize error
    norm_error = None
    if success is False:
        msg = str(error or "").strip()
        code = _classify_error(msg)
        if code not in _ALLOWED_ERROR_CODES:
            code = "RUNTIME_ERROR"
        norm_error = {"code": code, "message": msg[:160] if msg else ""}

    return bool(success), norm_result, norm_error


def _env_or_fail(var: str) -> str:
    val = os.environ.get(var)
    if not val:
        pytest.fail(f"Required environment variable {var} is not set.")
    return val


def _kubectl_exec(namespace: str, pod_name: str, user_cmd: str, timeout: float = 30.0) -> Tuple[str, int]:
    args = ["kubectl", "-n", namespace, "exec", pod_name, "--", "/bin/sh", "-lc", user_cmd]
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            text=True,
        )
        # If kubectl returns nonzero, prefer stderr as message but still return rc
        stdout = proc.stdout if proc.stdout else proc.stderr
        return stdout, proc.returncode
    except FileNotFoundError as e:
        raise RuntimeError("kubectl not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        return e.stdout or "", 124


# --------------------------------------------------------------------------------------
# Fixtures: import tool, resolve entry, create instances, and pod exec helper
# --------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def tool_module():
    mod_name = os.environ.get("PY_TOOL_MODULE", "tool_under_test")
    try:
        return importlib.import_module(mod_name)
    except Exception as e:
        pytest.fail(f"Failed to import module '{mod_name}': {e}")


@pytest.fixture(scope="session")
def tool_entry(tool_module):
    sym = os.environ.get("PY_TOOL_SYMBOL")
    candidate = None

    if sym:
        try:
            candidate = getattr(tool_module, sym)
        except AttributeError:
            pytest.fail(f"Symbol '{sym}' not found in module '{tool_module.__name__}'")

    if candidate is None:
        # Discover: class or object with an 'execute_tool' attribute (callable)
        for name in dir(tool_module):
            obj = getattr(tool_module, name)
            try:
                member = getattr(obj, "execute_tool", None)
                if callable(member):
                    candidate = obj
                    break
            except Exception:
                continue

    if candidate is None:
        pytest.fail("Could not discover a tool entry exposing a callable 'execute_tool'.")

    return candidate


@pytest.fixture(scope="session")
def tool_factory(tool_entry) -> Callable[[Dict[str, Any]], Any]:
    """
    Returns a callable that instantiates or returns the tool object with given config.
    """
    def _make(config: Dict[str, Any]):
        # If entry is a class, instantiate; if it's already an instance, just return it.
        if inspect.isclass(tool_entry):
            return tool_entry(config=config)
        # If it's a module- or instance-level object, try to pass config via attribute if supported.
        try:
            if hasattr(tool_entry, "__init__") and inspect.ismethod(getattr(tool_entry, "__init__")):
                return tool_entry
        except Exception:
            pass
        return tool_entry
    return _make


@pytest.fixture(scope="session")
def k8s_exec():
    """
    Returns a helper function to run a command inside the *valid* target pod via kubectl.
    Intended for physical verification on Happy Path.
    """
    namespace = _env_or_fail("K8S_NAMESPACE")
    pod_name = _env_or_fail("K8S_POD_NAME")

    def _exec(user_cmd: str, timeout: float = 30.0) -> Tuple[str, int]:
        return _kubectl_exec(namespace, pod_name, user_cmd, timeout=timeout)
    return _exec


# --------------------------------------------------------------------------------------
# Build parametrization with category marks (no private internals of pytest)
# --------------------------------------------------------------------------------------
def _marks_for_category(category: str):
    cat = (category or "").lower()
    marks = []
    if "happy" in cat:
        marks.append(pytest.mark.happy)
    elif "sad" in cat:
        marks.append(pytest.mark.sad)
    elif "security" in cat:
        marks.append(pytest.mark.security)
    return marks


CASES_PARAM = [
    pytest.param(c, id=c["case_name"], marks=_marks_for_category(c["category"]))
    for c in TEST_CASES
]


# --------------------------------------------------------------------------------------
# Test execution
# --------------------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES_PARAM)
async def test_k8s_tool_integration(case, tool_factory):
    """
    Core integration test that:
      - Instantiates the real tool using env-provided K8s context (or deliberately bad config).
      - Calls execute_tool(instance_id, parameters).
      - Canonicalizes the output.
      - For Happy Path: physically verifies echo semantics inside the pod.
    """
    category = case["category"].lower()
    params = case["parameters"]
    case_name = case["case_name"]
    suffix = _stable_hex(case_name, n=10)

    # Base (valid) config from env
    namespace = os.environ.get("K8S_NAMESPACE")
    pod_name = os.environ.get("K8S_POD_NAME")
    if not namespace or not pod_name:
        pytest.fail("K8S_NAMESPACE and K8S_POD_NAME must be set in the environment for these tests to run.")

    kubeconfig_path = os.environ.get("KUBECONFIG")
    base_config: Dict[str, Any] = {
        "namespace": namespace,
        "pod_name": pod_name,
        "kubeconfig_path": kubeconfig_path,
        "timeout": float(os.environ.get("K8S_TOOL_TIMEOUT", "30")),
    }

    cfg = dict(base_config)
    # Induce failures for Sad/Security categories via configuration, since inputs are ignored by the tool.
    if "sad" in category:
        if "pod_not_found" in case_name:
            cfg["pod_name"] = f"no-such-pod-{suffix}"
        elif "namespace_not_found" in case_name:
            cfg["namespace"] = f"no-such-ns-{suffix}"
        elif "timeout_tiny" in case_name:
            cfg["timeout"] = 0.001  # ~1ms
        elif "timeout_zeroish" in case_name:
            cfg["timeout"] = 0.0
        elif "bad_kubeconfig_path" in case_name:
            cfg["kubeconfig_path"] = f"/nonexistent/kubeconfig-{suffix}"
    elif "security" in category:
        # Simulate a "blocked context" by targeting non-existent resources.
        if "pod_missing" in case_name:
            cfg["pod_name"] = f"sec-missing-pod-{suffix}"
        else:
            cfg["namespace"] = f"sec-missing-ns-{suffix}"

    tool = tool_factory(cfg)

    # Prepare a unique, stable instance_id for the run.
    instance_id = f"pytest-{suffix}"

    # Invoke the tool. The entrypoint is async: execute_tool(instance_id, parameters)
    exec_coro = getattr(tool, "execute_tool", None)
    assert callable(exec_coro), "Tool entry does not expose a callable 'execute_tool'."

    t0 = time.time()
    try:
        tool_res = await exec_coro(instance_id=instance_id, parameters=params)
    except Exception as e:
        # If constructor works but invocation raises unexpected exceptions, normalize as runtime error.
        tool_res = SimpleNamespace(success=False, error=str(e), result=None, metrics={})

    success, norm_result, norm_error = _normalize_result(tool_res)

    # Record in RESULTS for final summary
    RESULTS.append(
        {
            "case_name": case_name,
            "success": success,
            "result": norm_result if success else None,
            "error": norm_error if not success else None,
        }
    )

    # Assertions per category
    if "happy" in category:
        assert success is True, f"Expected success for {case_name}, got failure: {norm_error}"
        # Validate core fields in normalized result (metrics should exist)
        assert isinstance(norm_result, dict) and "metrics" in norm_result, "Expected metrics in result."
        metrics = norm_result["metrics"]
        assert isinstance(metrics.get("rc", None), int), "Expected integer 'rc' metric."
        assert metrics.get("rc", None) == 0, "Pod command should return rc=0."
        # Physically verify echo semantics inside the real pod:
        # Compare stdout_size with an in-pod computed byte count for "ACK:mcp\n"
        # Using `printf` for stable newline behavior.
        stdout, rc = _kubectl_exec(namespace, pod_name, r'printf "ACK:mcp\n" | wc -c', timeout=30.0)
        assert rc == 0, f"kubectl exec failed while verifying echo semantics: {stdout}"
        m = re.search(r"(\d+)", stdout.strip())
        assert m, f"Unexpected wc output: {stdout}"
        expected_len = int(m.group(1))
        got_len = int(metrics.get("stdout_size", -1))
        assert got_len == expected_len, f"stdout_size mismatch: expected {expected_len}, got {got_len}"
        in_pod_cmd = str(metrics.get("in_pod_command", ""))
        assert "echo" in in_pod_cmd and "ACK:mcp" in in_pod_cmd, "In-pod command should contain echo ACK:mcp"

    else:
        # Sad Path / Security
        assert success is False, f"Expected failure for {case_name}, but tool succeeded."
        assert norm_error is not None and norm_error.get("message", ""), "Error message must be present."
        assert norm_error.get("code") in _ALLOWED_ERROR_CODES, f"Unexpected error code: {norm_error.get('code')}"

    # Soft test-level timeout check (best-effort; tool enforces its own timeout too)
    elapsed = time.time() - t0
    assert elapsed < 120, f"Test exceeded soft timeout: {elapsed:.1f}s"


# --------------------------------------------------------------------------------------
# Pytest hooks: emit exactly one JSON line (or write to RESULT_PATH)
# --------------------------------------------------------------------------------------
def pytest_sessionfinish(session, exitstatus):
    # Ensure consistent order
    ordered = sorted(RESULTS, key=lambda x: x["case_name"])
    payload = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    out_path = os.environ.get("RESULT_PATH")
    if out_path:
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:
            print(payload)
    else:
        print(payload)
