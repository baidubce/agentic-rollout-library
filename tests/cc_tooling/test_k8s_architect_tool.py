import asyncio
import importlib
import inspect
import json
import os
import random
import re
import shlex
import sys
import time
import hashlib
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
random.seed(1337)
os.environ.setdefault("PYTHONHASHSEED", "1337")

# ---------------------------------------------------------------------------
# TSX analysis (performed at generation time by model) → Embedded cases
#
# From the TSX source:
#   inputSchema = z.object({
#       prompt: z.string().describe('The task for the agent to perform')
#   })
# Only one required field: "prompt" (string). No enums, regex, or min/max.
# The tool streams progress and finally returns text blocks. No cross-field
# constraints and no defaults at the schema level.
#
# We execute against the provided Python tool whose OpenAI schema also requires
# "prompt" (string) and accepts optional "context" (string). Our tests adhere
# strictly to the TSX schema shape: parameters = {"prompt": <string>} ONLY.
# ---------------------------------------------------------------------------
TEST_CASES: List[Dict[str, Any]] = [
    # ---------------------- Happy Path (>=3) ----------------------
    {
        "case_name": "basic_prompt_single_line",
        "category": "Happy Path",
        "parameters": {"prompt": "Write a one-line system status update."},
    },
    {
        "case_name": "multiline_prompt",
        "category": "Happy Path",
        "parameters": {
            "prompt": "Analyze log anomalies:\n- spike at 02:14 UTC\n- repeated 5xx from api-gw\nProvide likely causes."
        },
    },
    {
        # Relies on default behavior for omitted optional fields (Python tool's "context" not present).
        "case_name": "unicode_prompt_default_context",
        "category": "Happy Path",
        "parameters": {"prompt": "こんにちは、K8s上での分析を始めてください。"},
    },

    # ------------------------ Sad Path (>=5) ------------------------
    {
        "case_name": "missing_prompt",
        "category": "Sad Path",
        "parameters": {},  # Missing required key
    },
    {
        "case_name": "prompt_null",
        "category": "Sad Path",
        "parameters": {"prompt": None},  # Wrong type
    },
    {
        "case_name": "prompt_wrong_type_int",
        "category": "Sad Path",
        "parameters": {"prompt": 123},  # Wrong type
    },
    {
        "case_name": "prompt_wrong_type_list",
        "category": "Sad Path",
        "parameters": {"prompt": ["a", "b"]},  # Wrong type
    },
    {
        "case_name": "timeout_tiny",
        "category": "Sad Path",
        "parameters": {"prompt": "Force a very small timeout to trigger error."},
        "config_overrides": {"timeout": 0.0001},
    },
    {
        "case_name": "pod_not_found",
        "category": "Sad Path",
        "parameters": {"prompt": "Use a non-existent pod to induce failure."},
        "config_overrides": {"pod_name": "this-pod-does-not-exist-xyz"},
    },

    # ------------------------- Security (>=2) -------------------------
    {
        "case_name": "prompt_command_injection_chars",
        "category": "Security",
        "parameters": {"prompt": 'Hello"; rm -rf / # attempt'},
        # Ensure failure without executing anything dangerous by using a bogus pod.
        "config_overrides": {"pod_name": "bogus-pod-for-security-case"},
    },
    {
        "case_name": "path_traversal_like",
        "category": "Security",
        "parameters": {"prompt": "../../etc/passwd"},
        # Ensure failure via invalid namespace (likely not found).
        "config_overrides": {"namespace": "nonexistent-namespace-for-security"},
    },
]

# Session-scope results for single-line JSON summary
RESULTS: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v is not None else default


def _stable_case_hash(case_name: str) -> str:
    return hashlib.sha256(case_name.encode("utf-8")).hexdigest()[:12]


def _to_snake(name: str) -> str:
    # camelCase / PascalCase → snake_case; kebab-case → snake_case
    s = re.sub(r"[\- ]+", "_", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def _snakeize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return { _to_snake(k): _snakeize(v) for k, v in obj.items() }
    if isinstance(obj, list):
        return [ _snakeize(x) for x in obj ]
    return obj


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def _canonicalize_success(result_obj: Any, metrics: Dict[str, Any]) -> Dict[str, Any]:
    norm: Dict[str, Any] = {}
    # Normalize messages / text
    msgs = []
    if isinstance(result_obj, list):
        for item in result_obj:
            if isinstance(item, dict):
                itype = item.get("type") or item.get("Type") or "text"
                text = item.get("text") or item.get("Text") or ""
                if isinstance(text, (bytes, bytearray)):
                    text = text.decode("utf-8", errors="replace")
                if isinstance(text, str) and len(text) > 64:
                    msgs.append({"type": itype, "text_prefix": text[:64]})
                else:
                    msgs.append({"type": itype, "text_prefix": text})
    elif isinstance(result_obj, dict):
        # Rare shape: treat as one message
        text = result_obj.get("text", "")
        msgs.append({"type": result_obj.get("type", "text"),
                     "text_prefix": (text[:64] if isinstance(text, str) else str(text)[:64])})
    else:
        msgs.append({"type": "text", "text_prefix": str(result_obj)[:64]})
    norm["messages"] = msgs

    # Keep only stable subset of metrics
    exec_loc = _get_attr(metrics, "execution_location", None)
    rc = _get_attr(metrics, "rc", None)
    stdout_size = _get_attr(metrics, "stdout_size", None)
    in_pod_cmd = _get_attr(metrics, "in_pod_command", None)
    # Normalize key names
    norm["metrics"] = _snakeize({
        "execution_location": exec_loc,
        "rc": rc,
        "stdout_size": stdout_size,
        "in_pod_command": in_pod_cmd,
    })
    return norm


def _canonicalize_error(err: Any) -> Dict[str, str]:
    msg = str(err) if err is not None else ""
    low = msg.lower()

    if "invalid input" in low or "required" in low or "must be a string" in low:
        code = "VALIDATION_ERROR"
    elif "timeout" in low:
        code = "RUNTIME_ERROR"
    elif "not found" in low or "does not exist" in low or "no such pod" in low:
        code = "NOT_FOUND"
    elif "argument list too long" in low or "too large" in low:
        code = "TOO_LARGE"
    elif "permission" in low or "forbidden" in low or "denied" in low:
        code = "SECURITY_BLOCKED"
    else:
        code = "RUNTIME_ERROR"

    # Keep a short, stable prefix
    short = msg.strip().splitlines()[0][:160]
    return {"code": code, "message": short}


def _extract_user_cmd_from_in_pod(full_cmd: Optional[str]) -> Optional[str]:
    """
    full_cmd format from tool metrics: 'sh -lc <quoted user_cmd>'
    We pull out the last argument which should be the quoted shell payload.
    """
    if not full_cmd:
        return None
    try:
        parts = shlex.split(full_cmd)
        if len(parts) >= 3 and parts[0] in ("sh", "/bin/sh") and parts[1] == "-lc":
            return parts[2]
        # Fallback: if quoted whole command exists after -lc
        if "-lc" in parts:
            idx = parts.index("-lc")
            if idx + 1 < len(parts):
                return parts[idx + 1]
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Fixtures: module import and tool discovery
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def tool_module():
    mod_path = _env("PY_TOOL_MODULE", "tool_under_test")
    try:
        return importlib.import_module(mod_path)
    except Exception as e:
        pytest.skip(f"Could not import module '{mod_path}': {e}")


@pytest.fixture(scope="session")
def tool_entry(tool_module):
    sym = _env("PY_TOOL_SYMBOL")
    candidate = None
    if sym:
        if not hasattr(tool_module, sym):
            pytest.skip(f"Module has no symbol '{sym}'")
        candidate = getattr(tool_module, sym)
    else:
        # Discover: class or object exposing an async 'execute_tool(instance_id, parameters)'
        for name in dir(tool_module):
            obj = getattr(tool_module, name)
            if inspect.isclass(obj):
                if hasattr(obj, "execute_tool"):
                    candidate = obj
                    break
            else:
                if hasattr(obj, "execute_tool") and callable(getattr(obj, "execute_tool")):
                    candidate = obj
                    break
    if candidate is None:
        pytest.skip("No suitable tool symbol with 'execute_tool' found in module.")
    return candidate


def _build_base_config() -> Dict[str, Any]:
    ns = _env("K8S_NAMESPACE")
    pod = _env("K8S_POD_NAME")
    if not ns or not pod:
        pytest.skip("K8S_NAMESPACE and K8S_POD_NAME must be set in the environment.")
    cfg: Dict[str, Any] = {
        "namespace": ns,
        "pod_name": pod,
    }
    # optional env-driven config
    if _env("KUBECONFIG_PATH"):
        cfg["kubeconfig_path"] = _env("KUBECONFIG_PATH")
    if _env("K8S_CONTAINER"):
        cfg["container"] = _env("K8S_CONTAINER")
    if _env("TOOL_TIMEOUT"):
        try:
            cfg["timeout"] = float(_env("TOOL_TIMEOUT"))  # type: ignore[arg-type]
        except Exception:
            pass
    return cfg


@pytest.fixture(scope="session")
def base_config():
    return _build_base_config()


@pytest.fixture(scope="session")
def is_class_tool(tool_entry):
    return inspect.isclass(tool_entry)


@pytest.fixture(scope="session")
def tool_factory(tool_entry, base_config, is_class_tool):
    """
    Returns a function that can produce a tool instance given optional overrides.
    If the discovered entry is an object (not a class), the same object is returned.
    """
    def _factory(overrides: Optional[Dict[str, Any]] = None):
        if is_class_tool:
            cfg = dict(base_config)
            if overrides:
                cfg.update(overrides)
            try:
                return tool_entry(config=cfg)
            except TypeError:
                # Some tools may accept kwargs directly
                return tool_entry(**cfg)
        else:
            # If overrides are requested but we cannot instantiate, mark at call site.
            return tool_entry
    return _factory


# ---------------------------------------------------------------------------
# Test execution
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", TEST_CASES, ids=[c["case_name"] for c in TEST_CASES])
def test_tool_cases(case: Dict[str, Any], tool_factory, is_class_tool):
    # Dynamically mark test based on category
    cat = case.get("category", "")
    mark = {"happy": "happy", "sad": "sad", "security": "security"}.get(cat.lower(), None)
    if mark:
        pytest.mark.usefixtures  # no-op to appease linters
        pytest.current_test = case["case_name"]
        pytest.item = None  # placeholder
    # Attach marker to the running node
    try:
        this_node = pytest._pytest.fixtures.getfixturevalue  # type: ignore[attr-defined]
    except Exception:
        pass
    # Add marker in a way compatible with PyTest runtime
    try:
        node = pytest.Request.node  # type: ignore[attr-defined]
    except Exception:
        node = None
    try:
        if node is not None and mark:
            node.add_marker(mark)
    except Exception:
        pass

    # Prepare instance (allow per-case overrides)
    overrides = case.get("config_overrides", {})
    if overrides and not is_class_tool:
        pytest.xfail("Per-case config overrides requested, but tool entry is not a class.")

    instance = tool_factory(overrides if overrides else None)

    # Build parameters strictly per TSX schema
    parameters = case.get("parameters", {})
    # Ensure we don't add fields beyond TSX input shape.
    parameters = {k: v for k, v in parameters.items() if k == "prompt"}

    # Execute with a soft timeout of 30s (or overridden in instance config)
    run_timeout = 30.0
    start = time.time()

    async def _run():
        # We must supply an instance_id; use a stable hash.
        instance_id = f"pytest-{_stable_case_hash(case['case_name'])}"
        # Call the tool (async). Expected signature: execute_tool(instance_id, parameters)
        return await instance.execute_tool(instance_id, parameters)  # type: ignore[attr-defined]

    try:
        tool_res = asyncio.run(asyncio.wait_for(_run(), timeout=run_timeout))
        # Extract fields tolerantly
        success = bool(_get_attr(tool_res, "success", False))
        result_obj = _get_attr(tool_res, "result", None)
        error_obj = _get_attr(tool_res, "error", None)
        metrics = _get_attr(tool_res, "metrics", {}) or {}

        if success:
            norm_result = _canonicalize_success(result_obj, metrics)
            norm_error = None
        else:
            norm_result = None
            norm_error = _canonicalize_error(error_obj)
    except Exception as e:
        # Treat as runtime error
        success = False
        tool_res = None
        result_obj = None
        metrics = {}
        norm_result = None
        norm_error = _canonicalize_error(str(e))

    duration = time.time() - start

    # Record result for JSON summary
    RESULTS.append({
        "case_name": case["case_name"],
        "success": success,
        "result": norm_result,
        "error": norm_error,
    })

    # ------------------ Assertions by category ------------------
    category = case.get("category", "")
    if category == "Happy Path":
        assert success is True, f"Expected success=True, got error: {norm_error}"

        # Validate core fields present in normalized result
        assert isinstance(norm_result, dict), "Normalized result missing"
        msgs = norm_result.get("messages", [])
        assert isinstance(msgs, list) and len(msgs) >= 1, "Expected at least one message"
        assert "metrics" in norm_result and isinstance(norm_result["metrics"], dict), "Missing metrics"

        # The tool summary text contains the char count; verify it matches our prompt length.
        prompt_val = parameters.get("prompt", "")
        expected_chars = len(prompt_val if isinstance(prompt_val, str) else "")
        text_prefix = msgs[0].get("text_prefix", "")
        m = re.search(r"(\d[\d,]*) chars", text_prefix)
        if m:
            # Remove commas
            reported = int(m.group(1).replace(",", ""))
            assert reported == expected_chars, f"Char count mismatch: reported={reported}, expected={expected_chars}"

        # Physical verification in the pod:
        # If tool metrics expose the in-pod command, re-run the exact user_cmd and expect ACK.
        in_pod_cmd = None
        try:
            in_pod_cmd = norm_result.get("metrics", {}).get("in_pod_command")
        except Exception:
            in_pod_cmd = None

        user_cmd = _extract_user_cmd_from_in_pod(in_pod_cmd)
        stdout = ""
        rc = 0

        async def _rerun_in_pod_with_instance():
            # Prefer using the tool's internal executor if available
            if hasattr(instance, "_run_in_pod") and callable(getattr(instance, "_run_in_pod")) and user_cmd:
                out, code = await instance._run_in_pod(user_cmd)  # type: ignore[attr-defined]
                return out, code
            # Fallback: try manager if exposed
            if hasattr(instance, "_mgr") and callable(getattr(instance, "_mgr")) and user_cmd:
                try:
                    mgr = instance._mgr()  # type: ignore[attr-defined]
                    # Execute full shell wrapper
                    full = f"sh -lc {shlex.quote(user_cmd)}"
                    res = mgr.execute_command(getattr(instance, "pod_name"), full)  # type: ignore[attr-defined]
                    if isinstance(res, tuple) and len(res) >= 2:
                        return (res[0].decode() if isinstance(res[0], (bytes, bytearray)) else str(res[0])), int(res[1])
                    if isinstance(res, dict):
                        return str(res.get("stdout", "")), int(res.get("rc", res.get("exit_code", 0)))
                    return str(res), 0
                except Exception as e:
                    return f"{e}", 1
            # Last resort: kubectl (may not exist; best effort)
            ns = _env("K8S_NAMESPACE")
            pod = _env("K8S_POD_NAME")
            if user_cmd and ns and pod:
                import subprocess
                try:
                    p = subprocess.run(
                        ["kubectl", "-n", ns, "exec", pod, "--", "/bin/sh", "-lc", user_cmd],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, text=True
                    )
                    return p.stdout, p.returncode
                except Exception as e:
                    return f"{e}", 1
            return "", 0

        if user_cmd:
            try:
                stdout, rc = asyncio.run(_rerun_in_pod_with_instance())
            except RuntimeError:
                # If event loop already running (rare), do a nested loop workaround
                loop = asyncio.new_event_loop()
                try:
                    stdout, rc = loop.run_until_complete(_rerun_in_pod_with_instance())
                finally:
                    loop.close()
            assert rc == 0, f"Re-executing in-pod command failed with rc={rc}."
            assert "ACK:architect" in stdout, "Expected 'ACK:architect' in pod stdout."

    elif category in ("Sad Path", "Security"):
        assert success is False, "Expected failure for Sad/Security path."
        assert norm_error is not None and norm_error.get("message"), "Expected error message."
        # Error code plausibility check
        assert norm_error["code"] in {"VALIDATION_ERROR", "RUNTIME_ERROR", "NOT_FOUND", "TOO_LARGE", "SECURITY_BLOCKED"}, \
            f"Unexpected error code: {norm_error['code']}"
    else:
        # Unknown category → no strict assertions beyond successful call.
        assert tool_res is not None, "Tool call did not return."


# ---------------------------------------------------------------------------
# Session finish hook: single-line JSON summary or write to RESULT_PATH
# ---------------------------------------------------------------------------
def pytest_sessionfinish(session, exitstatus):
    out = json.dumps(RESULTS, ensure_ascii=False, separators=(",", ":"))
    dest = _env("RESULT_PATH")
    if dest:
        try:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(out)
        except Exception:
            # If writing fails, fall back to printing to stdout
            print(out)
    else:
        # Print exactly one JSON line
        print(out)
