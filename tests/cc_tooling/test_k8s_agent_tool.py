# test_k8s_tool_integration.py
import asyncio
import hashlib
import importlib
import inspect
import json
import os
import random
import re
import string
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# -----------------------
# Determinism & constants
# -----------------------
random.seed(1337)
os.environ.setdefault("PYTHONHASHSEED", "1337")

SOFT_TIMEOUT_SECONDS = float(os.environ.get("TOOL_TIMEOUT", "30"))
RESULTS: List[Dict[str, Any]] = []

TEST_CASES: List[Dict[str, Any]] = [
    # ----------------
    # Happy Path (≥3)
    # ----------------
    {
        "case_name": "basic_prompt_no_context",
        "category": "Happy Path",
        "parameters": {
            "prompt": "List the contents of /etc (analysis only; do not execute).",
        },
    },
    {
        "case_name": "with_context_ignored_by_python_tool",
        "category": "Happy Path",
        "parameters": {
            "prompt": "Summarize recent log patterns.",
            "context": "Previous conversation: looked at /var/log last hour.",
        },
    },
    {
        "case_name": "empty_prompt_boundary",
        "category": "Happy Path",
        "parameters": {
            "prompt": "",
        },
    },
    {
        "case_name": "unicode_prompt",
        "category": "Happy Path",
        "parameters": {
            "prompt": "Check UTF-8 handling: 測試 ✅ — emojis and CJK.",
        },
    },
    # -------------
    # Sad Path (≥5)
    # -------------
    {
        "case_name": "missing_prompt_key",
        "category": "Sad Path",
        "parameters": {
            # Intentionally empty: TSX requires 'prompt'; Python tool validates presence.
        },
    },
    {
        "case_name": "prompt_wrong_type_int",
        "category": "Sad Path",
        "parameters": {  # wrong type
            "prompt": 12345,
        },
    },
    {
        "case_name": "prompt_wrong_type_bytes",
        "category": "Sad Path",
        "parameters": {  # wrong type
            "prompt": b"hello-bytes-not-string",
        },
    },
    {
        "case_name": "parameters_not_object_list",
        "category": "Sad Path",
        "parameters": [  # wrong container type altogether
            "not", "an", "object",
        ],
    },
    {
        "case_name": "prompt_none_value",
        "category": "Sad Path",
        "parameters": {  # wrong type: None
            "prompt": None,
        },
    },
    # ----------------
    # Security (≥2)
    # ----------------
    {
        "case_name": "prompt_cmd_injection_attempt",
        "category": "Security",
        "parameters": {
            "prompt": 'hello"; echo injected; sleep 0; # ; rm -rf /',
        },
    },
    {
        "case_name": "prompt_path_traversal_attempt",
        "category": "Security",
        "parameters": {
            "prompt": "../../etc/passwd",
        },
    },
]

# Build param marks for pytest
def _pytest_params():
    import pytest  # local import to keep top clean
    params = []
    for c in TEST_CASES:
        cat = (c.get("category") or "").lower()
        marks = []
        if cat == "happy path":
            marks.append(pytest.mark.happy)
        elif cat == "sad path":
            marks.append(pytest.mark.sad)
        elif cat == "security":
            marks.append(pytest.mark.security)
        params.append(pytest.param(c, id=c["case_name"], marks=marks))
    return params


# ----------------------
# Utility/helper methods
# ----------------------
def _stable_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


_SNAKE_RE_1 = re.compile("(.)([A-Z][a-z]+)")
_SNAKE_RE_2 = re.compile("([a-z0-9])([A-Z])")


def _to_snake_case(name: str) -> str:
    name = _SNAKE_RE_1.sub(r"\1_\2", name)
    name = _SNAKE_RE_2.sub(r"\1_\2", name)
    return name.replace("-", "_").lower()


def _snake_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return { _to_snake_case(k): _snake_keys(v) for k, v in obj.items() }
    if isinstance(obj, list):
        return [ _snake_keys(v) for v in obj ]
    return obj


def _canonicalize_result(res_obj: Any) -> Any:
    """
    Normalize tool result structures for stable comparisons.
    - For list of TextBlocks: reduce to text_prefix (64 chars)
    - For bytes/base64 (if ever present): just expose length/hash (not expected here)
    """
    try:
        if isinstance(res_obj, list):
            out = []
            for it in res_obj:
                if isinstance(it, dict):
                    t = it.get("text")
                    if isinstance(t, str):
                        out.append({"text_prefix": t[:64]})
                    else:
                        out.append({"text_prefix": str(t)[:64]})
                else:
                    out.append({"text_prefix": str(it)[:64]})
            return {"texts": out}
        if isinstance(res_obj, dict):
            return _snake_keys(res_obj)
        if isinstance(res_obj, (str, bytes)):
            s = res_obj if isinstance(res_obj, str) else "<bytes>"
            return {"text_prefix": str(s)[:64]}
        return res_obj
    except Exception as _:
        return {"_unparsed": str(type(res_obj))}


def _map_error_code(err_msg: Optional[str]) -> str:
    if not err_msg:
        return "RUNTIME_ERROR"
    m = err_msg.lower()
    if "invalid input" in m or "not iterable" in m or "must be a string" in m:
        return "VALIDATION_ERROR"
    if "timeout" in m:
        return "RUNTIME_ERROR"
    if "not found" in m:
        return "NOT_FOUND"
    if "denied" in m or "forbidden" in m or "blocked" in m or "security" in m:
        return "SECURITY_BLOCKED"
    return "RUNTIME_ERROR"


def _short_msg(msg: Optional[str], limit: int = 120) -> str:
    if not msg:
        return ""
    msg = re.sub(r"\s+", " ", msg).strip()
    return msg[:limit]


def _env_or_skip():
    import pytest
    ns = os.environ.get("K8S_NAMESPACE")
    pod = os.environ.get("K8S_POD_NAME")
    if not ns or not pod:
        pytest.skip("K8S_NAMESPACE and K8S_POD_NAME are required")
    return {
        "namespace": ns,
        "pod_name": pod,
        "kubeconfig_path": os.environ.get("KUBECONFIG"),
        "container": os.environ.get("K8S_CONTAINER") or None,
        "allow_dangerous": False,
        "timeout": float(os.environ.get("TOOL_TIMEOUT", "30")),
    }


def _load_tool_module():
    import pytest
    mod_name = os.environ.get("PY_TOOL_MODULE", "tool_under_test")
    try:
        return importlib.import_module(mod_name)
    except Exception as e:
        pytest.skip(f"Failed to import module {mod_name!r}: {e}")


def _discover_tool_entry(mod) -> Any:
    # If explicit symbol is provided, honor it.
    name = os.environ.get("PY_TOOL_SYMBOL")
    if name:
        if not hasattr(mod, name):
            raise AttributeError(f"PY_TOOL_SYMBOL={name} not found in module")
        return getattr(mod, name)

    # Else, heuristically find a class or object exposing an async execute_tool(instance_id, parameters, **kwargs)
    candidates: List[Tuple[str, Any]] = []
    for attr in dir(mod):
        obj = getattr(mod, attr)
        exec_attr = getattr(obj, "execute_tool", None)
        if callable(exec_attr) or inspect.iscoroutinefunction(exec_attr):
            candidates.append((attr, obj))

    if not candidates:
        raise RuntimeError("No suitable tool entry with execute_tool(...) found in module")

    # Prefer a class named like "*Tool", else first match
    for n, obj in candidates:
        if inspect.isclass(obj) and n.lower().endswith("tool"):
            return obj
    return candidates[0][1]


async def _run_tool_async(entry: Any, cfg: Dict[str, Any], instance_id: str, parameters: Any):
    """
    Instantiate (if class) and invoke execute_tool(instance_id, parameters).
    """
    instance = entry
    if inspect.isclass(entry):
        try:
            instance = entry(config=cfg)
        except TypeError:
            # Try plain constructor if it doesn't accept config kw
            instance = entry()
        except ImportError as e:
            # Likely missing kodo: propagate for skip handling
            raise

    # Execute with soft timeout
    coro = instance.execute_tool(instance_id, parameters)
    if not inspect.isawaitable(coro):
        # Defensive: some tools may return a coroutine-like wrapper
        raise TypeError("execute_tool(...) did not return an awaitable")
    return await asyncio.wait_for(coro, timeout=SOFT_TIMEOUT_SECONDS), instance


def _kubectl_exec(namespace: str, pod: str, cmd: str, container: Optional[str] = None, timeout: float = 10.0) -> Tuple[str, int]:
    args = ["kubectl", "-n", namespace, "exec", pod]
    if container:
        args += ["-c", container]
    args += ["--", "/bin/sh", "-lc", cmd]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return (proc.stdout + proc.stderr, proc.returncode)
    except Exception as e:
        return (str(e), 1)


# ----------------
# Pytest fixtures
# ----------------
import pytest


@pytest.fixture(scope="session")
def env_config():
    return _env_or_skip()


@pytest.fixture(scope="session")
def tool_module():
    return _load_tool_module()


@pytest.fixture(scope="session")
def tool_entry(tool_module):
    try:
        return _discover_tool_entry(tool_module)
    except Exception as e:
        pytest.skip(f"Tool entry discovery failed: {e}")


@pytest.fixture(scope="function")
def k8s_exec(env_config):
    """
    Returns a callable: run(cmd: str, instance: Optional[Any]) -> (stdout, rc)
    Uses the tool's own internal pod exec if available; else falls back to kubectl.
    """
    ns = env_config["namespace"]
    pod = env_config["pod_name"]
    container = env_config.get("container")

    async def _via_tool(instance: Any, user_cmd: str) -> Tuple[str, int]:
        # If the tool exposes an internal async _run_in_pod, use it
        runmeth = getattr(instance, "_run_in_pod", None)
        if callable(runmeth):
            return await asyncio.wait_for(runmeth(user_cmd), timeout=SOFT_TIMEOUT_SECONDS)
        # Fallback: kubectl
        out, rc = _kubectl_exec(ns, pod, user_cmd, container=container, timeout=min(SOFT_TIMEOUT_SECONDS, 15.0))
        return (out, rc)

    def _runner(cmd: str, instance: Optional[Any] = None) -> Tuple[str, int]:
        if instance is not None:
            try:
                return asyncio.run(_via_tool(instance, cmd))
            except Exception:
                # If tool path fails, fallback to kubectl
                pass
        return _kubectl_exec(ns, pod, cmd, container=container, timeout=min(SOFT_TIMEOUT_SECONDS, 15.0))

    return _runner


# --------------------------
# The main parameterized test
# --------------------------
@pytest.mark.parametrize("case", _pytest_params())
def test_k8s_tool(case, tool_entry, env_config, k8s_exec):
    """
    Executes each test case against the imported Python tool and performs assertions per category.
    Always records a canonicalized result into RESULTS for the final one-line JSON summary.
    """
    instance_id = "it-" + _stable_hash(case["case_name"])
    cfg = {
        "namespace": env_config["namespace"],
        "pod_name": env_config["pod_name"],
        "kubeconfig_path": env_config.get("kubeconfig_path"),
        "container": env_config.get("container"),
        "timeout": float(env_config.get("timeout") or SOFT_TIMEOUT_SECONDS),
        "allow_dangerous": False,
    }

    parameters = case.get("parameters")
    # Ensure we pass the object exactly as defined, including non-dicts for Sad Path coverage.
    # For dict-like, normalize to snake_case keys to match our own test invariants.
    if isinstance(parameters, dict):
        parameters = _snake_keys(parameters)

    # Run the tool (async)
    start = time.time()
    try:
        result_obj, instance = asyncio.run(_run_tool_async(tool_entry, cfg, instance_id, parameters))
    except ImportError as e:
        pytest.skip(f"Tool missing dependency (likely kodo): {e}")
    except asyncio.TimeoutError as e:
        # Record and assert as timeout
        success = False
        error = {"code": "RUNTIME_ERROR", "message": "Timeout while awaiting tool"}
        RESULTS.append({
            "case_name": case["case_name"],
            "success": False,
            "result": None,
            "error": error,
        })
        pytest.fail("Tool invocation timed out")
        return

    duration = time.time() - start

    # Duck-type the returned ToolResult
    success = bool(getattr(result_obj, "success", False))
    raw_result = getattr(result_obj, "result", None)
    raw_error = getattr(result_obj, "error", None)
    metrics = getattr(result_obj, "metrics", {}) or {}

    canon_result = _canonicalize_result(raw_result)
    error_obj = None
    if success:
        error_obj = None
    else:
        code = _map_error_code(str(raw_error) if raw_error is not None else "")
        error_obj = {"code": code, "message": _short_msg(str(raw_error))}

    # --------------- Assertions per category ---------------
    cat = (case.get("category") or "").lower()

    if cat == "happy path":
        assert success is True, f"Expected success on Happy Path, got error: {raw_error}"
        # Expected "Done (...)" style text in result text_prefix
        texts = canon_result.get("texts") if isinstance(canon_result, dict) else None
        assert texts and isinstance(texts, list), "Expected list of text results"
        assert isinstance(metrics, dict), "Expected metrics dict"
        assert int(metrics.get("rc", 0)) == 0, f"Expected rc=0, got {metrics.get('rc')}"
        in_cmd = str(metrics.get("in_pod_command", ""))
        assert "base64 -d >/dev/null" in in_cmd, "Expected base64 decode into /dev/null in pod command"
        # Physical verification: /dev/null exists and is character device; at least verify existence
        out, rc = k8s_exec("test -e /dev/null && echo OK || echo MISSING")
        assert rc == 0 and "OK" in out, f"/dev/null check failed: rc={rc}, out={out}"

    elif cat == "sad path":
        assert success is False, "Expected failure on Sad Path"
        assert error_obj and error_obj["message"], "Sad Path must include an error message"
        assert error_obj["code"] in {"VALIDATION_ERROR", "RUNTIME_ERROR", "NOT_FOUND"}, f"Unexpected error code: {error_obj['code']}"

    elif cat == "security":
        # Treat inputs as data only. The tool should not interpolate raw prompt into the shell command.
        in_cmd = str(metrics.get("in_pod_command", ""))
        prompt_str = None
        if isinstance(parameters, dict):
            prompt_str = parameters.get("prompt")
        elif isinstance(parameters, str):
            prompt_str = parameters
        else:
            prompt_str = str(parameters)

        if prompt_str:
            # Ensure raw prompt substrings (like 'rm -rf' or '../../etc/passwd') are NOT present in in_pod_command
            assert str(prompt_str) not in in_cmd, "Raw prompt leaked into in-pod shell command!"
            assert "base64 -d >/dev/null" in in_cmd, "Expected sanitized base64 transport"
        # Success or failure are both acceptable as long as no dangerous side effects are observed.
        # Verify no obvious side-effect file exists (best-effort): check a known filename pattern not created.
        token = _stable_hash(case["case_name"])
        out, rc = k8s_exec(f'ls /tmp 2>/dev/null | grep -E "pwned_{token}" || true')
        assert rc == 0 and out.strip() == "", "Unexpected side-effect artifact found in /tmp"

    else:
        pytest.fail(f"Unknown category: {case.get('category')}")

    # ------------------ Record canonicalized summary ------------------
    RESULTS.append({
        "case_name": case["case_name"],
        "success": success,
        "result": canon_result if success else None,
        "error": error_obj if not success else None,
    })


# ---------------------------
# One-line JSON summary hook
# ---------------------------
def pytest_sessionfinish(session, exitstatus):
    summary = json.dumps(RESULTS, ensure_ascii=False, separators=(",", ":"))
    result_path = os.environ.get("RESULT_PATH")
    if result_path:
        try:
            with open(result_path, "w", encoding="utf-8") as f:
                f.write(summary)
        except Exception:
            # If writing fails, fall back to stdout to avoid losing results
            print(summary)
    else:
        # Print exactly one JSON line
        print(summary)
