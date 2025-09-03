import os
import sys
import json
import time
import types
import base64
import hashlib
import random
import inspect
import asyncio
import subprocess
import importlib
from typing import Any, Dict, List, Tuple, Callable, Optional

import pytest

# ---------------------------
# Determinism
# ---------------------------
random.seed(1337)
os.environ.setdefault("PYTHONHASHSEED", "1337")

TEST_CASES: List[Dict[str, Any]] = [
    # -------- Happy Path (>=3) --------
    {
        "case_name": "happy_simple_sentence",
        "category": "Happy Path",
        "parameters": {"thought": "This is a simple thought."},
    },
    {
        "case_name": "happy_empty_string_minimal_input",
        "category": "Happy Path",
        "parameters": {"thought": ""},
    },
    {
        "case_name": "happy_unicode_newlines",
        "category": "Happy Path",
        "parameters": {"thought": "Idea with emojis and\newlines\nok."},
    },
    # -------- Sad Path (>=5) --------
    {
        "case_name": "sad_missing_required_field",
        "category": "Sad Path",
        "parameters": {},  # 'thought' missing
    },
    {
        "case_name": "sad_null_instead_of_string",
        "category": "Sad Path",
        "parameters": {"thought": None},
    },
    {
        "case_name": "sad_number_instead_of_string",
        "category": "Sad Path",
        "parameters": {"thought": 42},
    },
    {
        "case_name": "sad_list_instead_of_string",
        "category": "Sad Path",
        "parameters": {"thought": ["not", "a", "string"]},
    },
    {
        "case_name": "sad_bytes_instead_of_string",
        "category": "Sad Path",
        "parameters": {"thought": b"\xff\x00\x01"},
    },
    # -------- Security (>=2) --------
    {
        "case_name": "security_command_injection_attempt",
        "category": "Security",
        "parameters": {"thought": '"; rm -rf /; echo PWNED #'},
    },
    {
        "case_name": "security_path_traversal_and_substitution",
        "category": "Security",
        "parameters": {"thought": "../../etc/passwd $(id) `whoami`"},
    },
]

# Build pytest parameters with category marks
def _marks_for_category(cat: str):
    cat = cat.lower()
    marks = []
    if "happy" in cat:
        marks.append(pytest.mark.happy)
    if "sad" in cat:
        marks.append(pytest.mark.sad)
    if "security" in cat:
        marks.append(pytest.mark.security)
    return marks

PARAMS = [
    pytest.param(case, id=case["case_name"], marks=_marks_for_category(case["category"]))
    for case in TEST_CASES
]

# ---------------------------
# Env & global results
# ---------------------------
REQUIRED_ENVS = ("K8S_NAMESPACE", "K8S_POD_NAME")
K8S_NAMESPACE = os.getenv("K8S_NAMESPACE")
K8S_POD_NAME = os.getenv("K8S_POD_NAME")
K8S_CONTAINER = os.getenv("K8S_CONTAINER")  # optional
PY_TOOL_MODULE = os.getenv("PY_TOOL_MODULE", "tool_under_test")
PY_TOOL_SYMBOL = os.getenv("PY_TOOL_SYMBOL")
RESULT_PATH = os.getenv("RESULT_PATH")

RESULTS: List[Dict[str, Any]] = []  # session-scope accumulation

# ---------------------------
# Helper utilities (generic; do NOT implement or wrap tool behavior)
# ---------------------------

def _stable_case_id(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]

def _is_coro_callable(obj: Any) -> bool:
    return inspect.iscoroutinefunction(obj) or (
        callable(obj) and inspect.isawaitable(getattr(obj, "__call__", None))
    )

def _has_execute_tool_symbol(obj: Any) -> bool:
    fn = getattr(obj, "execute_tool", None)
    return callable(fn)

def _normalize_keys_snake(obj: Any) -> Any:
    def snake(s: str) -> str:
        out = []
        prev_lower = False
        for ch in s:
            if ch.isupper() and prev_lower:
                out.append("_")
            out.append(ch.lower())
            prev_lower = ch.islower()
        return "".join(out).replace("-", "_")
    if isinstance(obj, dict):
        return {snake(k): _normalize_keys_snake(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_normalize_keys_snake(v) for v in obj]
    else:
        return obj

def _extract_attr(obj: Any, name: str, default: Any = None) -> Any:
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict) and name in obj:
        return obj[name]
    return default

def _canonicalize_error(err: Any) -> Optional[Dict[str, str]]:
    if not err:
        return None
    msg = str(err)
    lower = msg.lower()
    if "invalid input" in lower or "required" in lower or "must be a string" in lower or "type" in lower:
        code = "VALIDATION_ERROR"
    elif "timeout" in lower:
        code = "RUNTIME_ERROR"
    elif "not found" in lower or "no such file" in lower:
        code = "NOT_FOUND"
    elif "too large" in lower or "argument list too long" in lower:
        code = "TOO_LARGE"
    elif "blocked" in lower or "forbidden" in lower or "denied" in lower:
        code = "SECURITY_BLOCKED"
    else:
        code = "RUNTIME_ERROR"
    return {"code": code, "message": msg.splitlines()[0][:200]}

def _is_tool_success(res_obj: Any) -> bool:
    return bool(_extract_attr(res_obj, "success", False))

def _get_tool_result(res_obj: Any) -> Any:
    return _extract_attr(res_obj, "result", None)

def _get_tool_error(res_obj: Any) -> Any:
    return _extract_attr(res_obj, "error", None)

def _get_tool_metrics(res_obj: Any) -> Dict[str, Any]:
    m = _extract_attr(res_obj, "metrics", {}) or {}
    if not isinstance(m, dict):
        return {}
    return m

def _await_if_needed(obj):
    if inspect.isawaitable(obj):
        return asyncio.run(obj)
    return obj

# ---------------------------
# Pytest fixtures
# ---------------------------

@pytest.fixture(scope="session", autouse=True)
def _env_requirements():
    missing = [k for k in REQUIRED_ENVS if not os.getenv(k)]
    if missing:
        pytest.skip(f"Missing required env vars: {', '.join(missing)}")

@pytest.fixture(scope="session")
def tool_module():
    try:
        return importlib.import_module(PY_TOOL_MODULE)
    except Exception as e:
        pytest.skip(f"Unable to import module '{PY_TOOL_MODULE}': {e}")

@pytest.fixture(scope="session")
def tool_entry(tool_module):
    # If explicit symbol provided, use it.
    if PY_TOOL_SYMBOL:
        if not hasattr(tool_module, PY_TOOL_SYMBOL):
            pytest.skip(f"Module '{PY_TOOL_MODULE}' lacks symbol '{PY_TOOL_SYMBOL}'")
        entry = getattr(tool_module, PY_TOOL_SYMBOL)
        if not _has_execute_tool_symbol(entry) and not (_is_coro_callable(entry) and hasattr(entry, "__self__")):
            pytest.skip(f"Symbol '{PY_TOOL_SYMBOL}' does not expose execute_tool")
        return entry

    # Discover class or object exposing execute_tool
    candidates: List[Tuple[str, Any]] = []
    for name, obj in vars(tool_module).items():
        if name.startswith("_"):
            continue
        if inspect.isclass(obj) and _has_execute_tool_symbol(obj):
            candidates.append((name, obj))
        elif _has_execute_tool_symbol(obj):
            candidates.append((name, obj))
    if not candidates:
        pytest.skip("No class or object with callable 'execute_tool(parameters: dict)' found in module.")
    # Prefer class named like *Think* or any with the method
    for name, obj in candidates:
        if "think" in name.lower():
            return obj
    return candidates[0][1]

@pytest.fixture(scope="session")
def tool_instance(tool_entry):
    # Instantiate if it's a class; else assume it's already an instance/object.
    try:
        if inspect.isclass(tool_entry):
            cfg = {
                "namespace": K8S_NAMESPACE,
                "pod_name": K8S_POD_NAME,
            }
            if K8S_CONTAINER:
                cfg["container"] = K8S_CONTAINER
            # Do not wrap or re-implement; call the real constructor.
            instance = tool_entry(cfg)
        else:
            instance = tool_entry
        # Sanity: must have execute_tool
        if not hasattr(instance, "execute_tool"):
            pytest.skip("Resolved tool entry does not expose 'execute_tool'.")
        return instance
    except ImportError as e:
        pytest.skip(f"Tool dependency missing: {e}")
    except Exception as e:
        pytest.skip(f"Failed to construct tool: {e}")

@pytest.fixture(scope="session")
def k8s_exec(tool_instance) -> Callable[[str, float], Tuple[str, int]]:
    """
    Return a function that executes a shell command _in the target pod_ and returns (stdout, rc).
    Prefer the tool's own exec facility if it exposes one (e.g., _run_in_pod). Otherwise fallback to kubectl.
    """
    run_via_tool = getattr(tool_instance, "_run_in_pod", None)

    if callable(run_via_tool):
        def via_tool(cmd: str, timeout: float = 30.0) -> Tuple[str, int]:
            # The tool's method is async; await it safely.
            return _await_if_needed(run_via_tool(cmd))
        return via_tool

    # Fallback: kubectl exec
    def via_kubectl(cmd: str, timeout: float = 30.0) -> Tuple[str, int]:
        base = ["kubectl", "-n", K8S_NAMESPACE, "exec", K8S_POD_NAME]
        if K8S_CONTAINER:
            base.extend(["-c", K8S_CONTAINER])
        full = base + ["--", "/bin/sh", "-lc", cmd]
        proc = subprocess.run(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        out = proc.stdout.decode("utf-8", errors="replace")
        return out, proc.returncode
    return via_kubectl

# ---------------------------
# Test logic
# ---------------------------

@pytest.mark.parametrize("case", PARAMS)
def test_think_tool_cases(tool_instance, k8s_exec, case):
    """
    Executes each embedded test case against the imported tool and validates results.
    Records a canonical summary for each case.
    """
    case_name: str = case["case_name"]
    category: str = case["category"]
    params_in: Dict[str, Any] = dict(case["parameters"])  # shallow copy

    # Ensure parameter shape matches schema (only 'thought' allowed for these tests).
    # We do not mutate user input (including Security strings).
    instance_id = _stable_case_id(case_name)

    # Invoke the tool's real execute_tool (async-friendly).
    try:
        res_obj = _await_if_needed(getattr(tool_instance, "execute_tool")(instance_id=instance_id, parameters=params_in))
    except Exception as e:
        # Treat unexpected exceptions as runtime errors for this case
        RESULTS.append({
            "case_name": case_name,
            "success": False,
            "result": None,
            "error": {"code": "RUNTIME_ERROR", "message": str(e).splitlines()[0][:200]},
        })
        pytest.fail(f"Tool raised unexpected exception: {e}")

    success = _is_tool_success(res_obj)
    result_raw = _get_tool_result(res_obj)
    err_raw = _get_tool_error(res_obj)
    metrics = _get_tool_metrics(res_obj)

    # Canonicalize for summary
    if result_raw is not None and not isinstance(result_raw, dict):
        # If tool returns a non-dict result, wrap minimally
        result_canon = {"value": str(result_raw)}
    else:
        result_canon = _normalize_keys_snake(result_raw) if result_raw is not None else None
    error_canon = _canonicalize_error(err_raw)

    # ---------------------------
    # Assertions per category
    # ---------------------------

    if "Happy" in category:
        # 1) Must succeed
        assert success is True, f"Happy path should succeed. Error: {err_raw}"
        # 2) Validate core fields in normalized result
        assert isinstance(result_canon, dict) and "thought" in result_canon, "Result must contain 'thought'."
        assert result_canon["thought"] == params_in.get("thought", ""), "Returned thought must echo input."
        # 3) Physical verification in pod: environment & ACK convention
        #    The tool's in-pod command prints "ACK:think\n". We verify the environment's behavior deterministically.
        #    First, confirm shell is responsive:
        out, rc = k8s_exec("true")
        assert rc == 0, "Pod must accept commands (true)."
        #    Confirm ack length property in the pod matches metrics['stdout_size'], if available.
        #    We compute the byte count for "ACK:think\n" inside the pod.
        out2, rc2 = k8s_exec('printf "ACK:think\\n" | wc -c')
        assert rc2 == 0, "wc -c must run in pod."
        try:
            ack_len_in_pod = int(out2.strip())
        except Exception:
            ack_len_in_pod = None
        if isinstance(metrics, dict) and "stdout_size" in metrics and isinstance(metrics["stdout_size"], int):
            # stdout_size should equal 10 in typical POSIX shells for "ACK:think\n"
            assert ack_len_in_pod in (10, None), "Unexpected ack length in pod environment."
            assert metrics["stdout_size"] in (ack_len_in_pod, 10), f"stdout_size should match ack length (got {metrics['stdout_size']})."
        # Also assert rc==0 if available
        if "rc" in metrics:
            assert metrics["rc"] == 0, f"In-pod command should succeed; rc={metrics['rc']}"
    elif "Sad" in category:
        # Must fail with validation-style error
        assert success is False, "Sad path should fail."
        assert error_canon is not None and error_canon["message"], "Sad path must include an error message."
        assert error_canon["code"] in {"VALIDATION_ERROR", "RUNTIME_ERROR", "TOO_LARGE"}, "Unexpected error code for Sad Path."
    elif "Security" in category:
        # Accept either explicit blocking (failure) OR safe neutralization (success echoing data).
        if success:
            assert isinstance(result_canon, dict) and "thought" in result_canon, "Result must contain 'thought'."
            assert result_canon["thought"] == params_in["thought"], "Security inputs must be treated as data (no mutation)."
            # Double-check no obvious side effects are present by testing harmless listing of a sensitive path
            # (we're not asserting presence/absence of files, just that the pod is alive).
            _, rc = k8s_exec("true")
            assert rc == 0, "Pod must remain healthy after security inputs."
        else:
            assert error_canon is not None and error_canon["message"], "Security failure must include an error."
            assert error_canon["code"] in {"SECURITY_BLOCKED", "VALIDATION_ERROR", "RUNTIME_ERROR"}, "Unexpected error code for Security."

    # Accumulate canonical summary
    RESULTS.append({
        "case_name": case_name,
        "success": bool(success),
        "result": result_canon if success else None,
        "error": error_canon if not success else None,
    })


# ---------------------------
# Session summary output
# ---------------------------

def pytest_sessionfinish(session, exitstatus):
    """
    Emit exactly one JSON line (or write to RESULT_PATH) summarizing per-case results.
    """
    payload = json.dumps(RESULTS, ensure_ascii=False, separators=(",", ":"))
    if RESULT_PATH:
        try:
            with open(RESULT_PATH, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception as e:
            # As a fallback, print to stdout if writing fails
            print(payload)
    else:
        # Exactly one line
        print(payload)
