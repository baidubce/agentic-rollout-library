import os
import sys
import json
import shlex
import time
import asyncio
import random
import hashlib
import inspect
import importlib
import subprocess
from typing import Any, Dict, List, Tuple, Optional

import pytest

# --- Determinism ---
random.seed(1337)
os.environ.setdefault("PYTHONHASHSEED", "1337")

# --- Global results sink for one-line summary ---
RESULTS: List[Dict[str, Any]] = []

TEST_CASES: List[Dict[str, Any]] = [
    # Happy Path (≥3, including one relying on a "default-ish" boundary: empty string still accepted)
    {
        "case_name": "happy_basic_trigger",
        "category": "Happy Path",
        "parameters": {"trigger": "open_form"},
    },
    {
        "case_name": "happy_empty_trigger_boundary_defaultish",
        "category": "Happy Path",
        "parameters": {"trigger": ""},
    },
    {
        "case_name": "happy_unicode_trigger",
        "category": "Happy Path",
        "parameters": {
                 "trigger": "\u30b9\u30c6\u30c3\u30ab\u30fc\u304a\u9858\u3044\u3057\u307e\u3059\U0001F680"
                        },
    },

    # Sad Path (≥5)
    {
        "case_name": "sad_missing_trigger",
        "category": "Sad Path",
        "parameters": {},
    },
    {
        "case_name": "sad_null_trigger",
        "category": "Sad Path",
        "parameters": {"trigger": None},
    },
    {
        "case_name": "sad_non_string_integer",
        "category": "Sad Path",
        "parameters": {"trigger": 123},
    },
    {
        "case_name": "sad_non_string_boolean",
        "category": "Sad Path",
        "parameters": {"trigger": True},
    },
    {
        "case_name": "sad_non_string_list",
        "category": "Sad Path",
        "parameters": {"trigger": ["not", "a", "string"]},
    },

    # Security (≥2) — attempt shell/meta injections; must be treated as data by the tool.
    # Use unique file paths derived from case_name to verify *no* side effects occurred.
    {
        "case_name": "security_injection_semicolons_touch",
        "category": "Security",
        "parameters": {"trigger": '"; touch /tmp/OWNED_{{CASEHASH}}; echo "'},
    },
    {
        "case_name": "security_injection_subshell_and_redir",
        "category": "Security",
        "parameters": {"trigger": '$(touch /tmp/PWNED_{{CASEHASH}}) || echo kept_safe'},
    },
]

# --- Helpers ---

def _stable_hash(s: str, n: int = 8) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]

def _snake(s: str) -> str:
    out = []
    for i, ch in enumerate(s):
        if ch.isupper() and i > 0 and (s[i-1].islower() or (i+1 < len(s) and s[i+1].islower())):
            out.append("_")
        out.append(ch.lower())
    return "".join(out).replace("-", "_")

def _to_snake_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return { _snake(k): _to_snake_keys(v) for k, v in obj.items() }
    if isinstance(obj, list):
        return [ _to_snake_keys(v) for v in obj ]
    return obj

def _map_error_code(msg: str, category: str) -> str:
    if not msg:
        return "RUNTIME_ERROR"
    m = msg.lower()
    if "invalid input" in m or "is required" in m or "must be a string" in m:
        return "VALIDATION_ERROR"
    if "timeout" in m:
        return "RUNTIME_ERROR"
    if "permission" in m or "blocked" in m or "denied" in m:
        return "SECURITY_BLOCKED"
    if "not found" in m or "no such file" in m:
        return "NOT_FOUND"
    # For security category with success=False but generic error, mark as SECURITY_BLOCKED conservatively
    if category == "Security":
        return "SECURITY_BLOCKED"
    return "RUNTIME_ERROR"

def _normalize_output(success: bool, result: Any, error: Any) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    norm_result: Optional[Dict[str, Any]] = None
    norm_error: Optional[Dict[str, Any]] = None
    if success:
        # Keep only semantic fields from result (if dict-like); drop volatile metrics if present inside.
        if isinstance(result, dict):
            # Common simple structure for this tool: {"success": True}
            norm_result = _to_snake_keys({k: v for k, v in result.items() if k in ("success",)})
        else:
            norm_result = {"text_prefix": str(result)[:64]}
    else:
        # Normalize error shape
        if isinstance(error, str):
            norm_error = {"code": _map_error_code(error, "Sad Path"), "message": error.splitlines()[0][:160]}
        elif isinstance(error, dict):
            msg = str(error.get("message") or error.get("error") or "")
            norm_error = {"code": _map_error_code(msg, "Sad Path"), "message": msg[:160] if msg else str(error)[:160]}
        else:
            norm_error = {"code": "RUNTIME_ERROR", "message": str(error)[:160] if error else "unknown error"}
    return norm_result, norm_error

def _get_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v

def _kubectl_exec(namespace: str, pod: str, command: str, timeout: int = 30) -> Tuple[str, int]:
    # Use kubectl; never reaches external networks beyond the K8s API server.
    base = ["kubectl", "exec", "-n", namespace, pod, "--", "/bin/sh", "-lc", command]
    try:
        proc = subprocess.run(base, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
        stdout = proc.stdout.decode("utf-8", errors="replace")
        return stdout, int(proc.returncode)
    except subprocess.TimeoutExpired:
        return "", 124

# --- Pytest fixtures ---

@pytest.fixture(scope="session")
def cluster_env():
    # Validate required env upfront for clearer failures.
    try:
        ns = _get_env("K8S_NAMESPACE")
        pod = _get_env("K8S_POD_NAME")
    except RuntimeError as e:
        pytest.skip(str(e))
    return {"namespace": ns, "pod_name": pod, "container": os.environ.get("K8S_CONTAINER")}

@pytest.fixture(scope="session")
def tool_module():
    module_path = os.environ.get("PY_TOOL_MODULE", "tool_under_test")
    try:
        return importlib.import_module(module_path)
    except Exception as e:
        pytest.skip(f"Failed to import PY_TOOL_MODULE={module_path}: {e}")

@pytest.fixture(scope="session")
def tool_entry(tool_module):
    symbol = os.environ.get("PY_TOOL_SYMBOL")
    if symbol:
        entry = getattr(tool_module, symbol, None)
        if entry is None:
            pytest.skip(f"PY_TOOL_SYMBOL '{symbol}' not found in module.")
        return entry

    # Discover a class or object exposing an async or sync 'execute_tool' attribute.
    candidates = []
    for name, obj in vars(tool_module).items():
        if name.startswith("_"):
            continue
        # Class with execute_tool
        if inspect.isclass(obj) and hasattr(obj, "execute_tool"):
            candidates.append(obj)
        # Instance/object with execute_tool
        elif hasattr(obj, "execute_tool"):
            candidates.append(obj)
    if not candidates:
        pytest.skip("No suitable tool entry with 'execute_tool' found in module.")
    # Prefer classes over objects for cleaner instantiation/configuration
    cls = None
    for c in candidates:
        if inspect.isclass(c):
            cls = c
            break
    return cls or candidates[0]

@pytest.fixture(scope="session")
def is_class(tool_entry):
    return inspect.isclass(tool_entry)

@pytest.fixture(scope="function")
def tool_instance(tool_entry, is_class, cluster_env):
    # Instantiate with K8s context; rely purely on the tool's constructor.
    config = {
        "namespace": cluster_env["namespace"],
        "pod_name": cluster_env["pod_name"],
        "container": cluster_env["container"],
    }
    if is_class:
        try:
            instance = tool_entry(config=config)  # type: ignore[call-arg]
        except TypeError:
            # Fallback: some tools may accept kwargs directly
            instance = tool_entry(**config)  # type: ignore[misc]
    else:
        instance = tool_entry
        # If the object supports a 'configure' method, we won't call it (no shims).
    return instance

@pytest.fixture(scope="session")
def k8s_exec(cluster_env):
    namespace = cluster_env["namespace"]
    pod = cluster_env["pod_name"]

    def _exec(cmd: str, timeout: int = 30) -> Tuple[str, int]:
        return _kubectl_exec(namespace, pod, cmd, timeout)
    return _exec

# --- Parametrization ---

@pytest.fixture(params=TEST_CASES, ids=[c["case_name"] for c in TEST_CASES])
def case(request):
    # Attach category marks dynamically for filtering.
    cat = request.param["category"].lower().split()[0]
    request.node.add_marker(getattr(pytest.mark, cat, pytest.mark.usefixtures()))
    return request.param

# --- Core test ---

@pytest.mark.timeout(int(os.environ.get("TEST_TIMEOUT", "60")))
def test_tool_against_pod(case, tool_instance, k8s_exec, cluster_env):
    """Runs all embedded cases against the imported tool; verifies behavior & pod safety."""
    category = case["category"]
    case_name = case["case_name"]
    casehash = _stable_hash(case_name, 10)
    params: Dict[str, Any] = dict(case["parameters"])

    # For security cases, embed a unique filename into payload attempts to later verify absence.
    unique_flag = f"/tmp/SEC_{casehash}.flag"
    if category == "Security":
        for k, v in list(params.items()):
            if isinstance(v, str):
                params[k] = v.replace("{{CASEHASH}}", casehash).replace("/tmp/OWNED", unique_flag).replace("/tmp/PWNED", unique_flag)

    # Build instance_id deterministically
    instance_id = f"it_{casehash}"

    # Execute tool (async-safe)
    async def _run():
        # Prefer calling optional lifecycle hooks if present (no shims)
        if hasattr(tool_instance, "_initialize_instance") and inspect.iscoroutinefunction(getattr(tool_instance, "_initialize_instance")):
            await getattr(tool_instance, "_initialize_instance")(instance_id)
        try:
            if inspect.iscoroutinefunction(getattr(tool_instance, "execute_tool")):
                return await getattr(tool_instance, "execute_tool")(instance_id, params)
            else:
                # Support sync implementations if any
                return getattr(tool_instance, "execute_tool")(instance_id, params)
        finally:
            if hasattr(tool_instance, "_cleanup_instance") and inspect.iscoroutinefunction(getattr(tool_instance, "_cleanup_instance")):
                await getattr(tool_instance, "_cleanup_instance")(instance_id)

    try:
        res = asyncio.run(asyncio.wait_for(_run(), timeout=float(os.environ.get("TEST_TIMEOUT", "60"))))
    except asyncio.TimeoutError:
        # Record timeout as a runtime error
        RESULTS.append({
            "case_name": case_name,
            "success": False,
            "result": None,
            "error": {"code": "RUNTIME_ERROR", "message": "timeout"},
        })
        assert False, f"Case {case_name}: timed out"
        return

    # Extract fields from ToolResult-like object
    success = bool(getattr(res, "success", getattr(res, "ok", False)))
    result_payload = getattr(res, "result", None)
    error_payload = getattr(res, "error", None)
    metrics = getattr(res, "metrics", {}) or {}

    # Canonicalize for summary
    norm_result, norm_error = _normalize_output(success, result_payload, error_payload)

    # --- Assertions per category ---
    if category == "Happy Path":
        assert success is True, f"{case_name}: expected success True, got {success} with error={error_payload}"
        assert isinstance(result_payload, dict) and result_payload.get("success") is True, f"{case_name}: result missing success=true"

        # Physical verification: re-run the in-pod command recorded by the tool to ensure expected behavior.
        # The tool stores the exact shell it ran in metrics['in_pod_command'] (e.g., "sh -lc '...; echo \"ACK:sticker\"'")
        in_cmd = metrics.get("in_pod_command")
        assert isinstance(in_cmd, str) and len(in_cmd) > 0, f"{case_name}: missing in_pod_command metric"
        # Execute the recorded command inside the pod (nested /bin/sh -lc is fine)
        out, rc = k8s_exec(in_cmd, timeout=30)
        assert rc == 0, f"{case_name}: re-exec of in_pod_command failed rc={rc}"
        assert "ACK:sticker" in out, f"{case_name}: expected 'ACK:sticker' in stdout"

    elif category == "Sad Path":
        assert success is False, f"{case_name}: expected failure for sad path; got success True"
        assert error_payload, f"{case_name}: expected error message for sad path"
        # Map error code
        norm_error = {"code": _map_error_code(str(error_payload), "Sad Path"),
                      "message": str(error_payload).splitlines()[0][:160]}

    elif category == "Security":
        # Security inputs should be treated as data. We verify *no* file got created in the pod.
        # Regardless of success/failure, ensure the unique flag file does not exist.
        probe_cmd = f'test -f {shlex.quote(unique_flag)} && echo "__PWNED__" || echo "__SAFE__"'
        out, rc = k8s_exec(probe_cmd, timeout=30)
        assert "__SAFE__" in out, f"{case_name}: potential injection side-effect detected at {unique_flag}"
        # We accept either success or failure, but ensure no side effects and, if success, that 'ACK:sticker' behavior holds.
        if success:
            in_cmd = metrics.get("in_pod_command")
            assert isinstance(in_cmd, str) and len(in_cmd) > 0, f"{case_name}: missing in_pod_command metric"
            out2, rc2 = k8s_exec(in_cmd, timeout=30)
            assert rc2 == 0 and "ACK:sticker" in out2, f"{case_name}: unexpected behavior on re-exec for security case"
        else:
            assert error_payload, f"{case_name}: on failure, expected error payload"

    # Record for one-line summary
    RESULTS.append({
        "case_name": case_name,
        "success": success,
        "result": norm_result if success else None,
        "error": None if success else norm_error,
    })

# --- Session summary emitter ---

def pytest_sessionfinish(session, exitstatus):
    # Write to RESULT_PATH if set; else print exactly one JSON line.
    payload = json.dumps(RESULTS, ensure_ascii=False)
    dest = os.environ.get("RESULT_PATH")
    try:
        if dest:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(payload)
        else:
            # Print only one line. Pytest may still print its own summary; -q is recommended.
            sys.stdout.write(payload + "\n")
    except Exception:
        # Last-resort: avoid crashing the session finish
        pass
