import asyncio
import hashlib
import importlib
import inspect
import json
import os
import random
import shlex
import subprocess
import sys
from typing import Any, Dict, List, Tuple

import pytest

# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
random.seed(1337)
os.environ.setdefault("PYTHONHASHSEED", "1337")

# ---------------------------------------------------------------------------
# Embedded TEST_CASES derived from the TSX schema/behavior at generation time
# Schema: strict object with a single optional string field `file_path`.
# Behavior:
# - If `file_path` is provided (truthy), read the file inside MEMORY_DIR; error if missing.
# - If not provided (or falsy like ""), list files and include index.md content.
# - Paths must remain inside MEMORY_DIR; traversal like "../" is rejected.
# ---------------------------------------------------------------------------
TEST_CASES: List[Dict[str, Any]] = [
    # ----------------------- Happy Path (>=3) -----------------------
    {
        "case_name": "list_directory_default",
        "category": "Happy Path",
        "parameters": {},
    },
    {
        "case_name": "read_alpha_txt",
        "category": "Happy Path",
        "parameters": {"file_path": "notes/alpha.txt"},
    },
    {
        "case_name": "read_index_md",
        "category": "Happy Path",
        "parameters": {"file_path": "index.md"},
    },
    # ------------------------- Sad Path (>=5) ------------------------
    {
        "case_name": "file_not_found",
        "category": "Sad Path",
        "parameters": {"file_path": "nope.txt"},
    },
    {
        "case_name": "file_is_directory",
        "category": "Sad Path",
        "parameters": {"file_path": "notes"},
    },
    {
        "case_name": "file_path_wrong_type_number",
        "category": "Sad Path",
        "parameters": {"file_path": 42},
    },
    {
        "case_name": "file_path_wrong_type_object",
        "category": "Sad Path",
        "parameters": {"file_path": {"unexpected": "object"}},
    },
    {
        "case_name": "file_path_whitespace",
        "category": "Sad Path",
        "parameters": {"file_path": "   "},
    },
    # -------------------------- Security (>=2) -----------------------
    {
        "case_name": "path_traversal_upwards",
        "category": "Security",
        "parameters": {"file_path": "../../etc/passwd"},
    },
    {
        "case_name": "path_traversal_parent_only",
        "category": "Security",
        "parameters": {"file_path": "../"},
    },
]

# Build pytest parameters with dynamic marks
TEST_PARAMS = []
for c in TEST_CASES:
    marks = []
    if c["category"] == "Happy Path":
        marks.append(pytest.mark.happy)
    elif c["category"] == "Sad Path":
        marks.append(pytest.mark.sad)
    elif c["category"] == "Security":
        marks.append(pytest.mark.security)
    TEST_PARAMS.append(pytest.param(c, id=c["case_name"], marks=marks))

# ---------------------------------------------------------------------------
# Global results collector for one-line JSON summary
# ---------------------------------------------------------------------------
RESULTS: List[Dict[str, Any]] = []

# ---------------------------------------------------------------------------
# Utility helpers (test-only; do NOT implement tool behavior)
# ---------------------------------------------------------------------------
def _to_dictish(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    # Try common attribute names without importing tool types
    out: Dict[str, Any] = {}
    for k in ("success", "result", "error", "metrics"):
        if hasattr(obj, k):
            out[k] = getattr(obj, k)
    return out


def _normalize_success_result(result: Dict[str, Any]) -> Dict[str, Any]:
    # Unify to snake_case keys and compress large text
    out: Dict[str, Any] = {}
    if not isinstance(result, dict):
        return out
    content = result.get("content")
    if isinstance(content, (bytes, bytearray)):
        h = hashlib.sha256(content).hexdigest()
        out["content"] = {"base64_len": len(content), "sha256": h}
    elif isinstance(content, str):
        out["content"] = {"text_prefix": content[:64]}
    return out


def _map_error_code(msg: str) -> str:
    m = (msg or "").lower()
    if "invalid input" in m or "must be a string" in m:
        return "VALIDATION_ERROR"
    if "invalid memory file path" in m:
        return "SECURITY_BLOCKED"
    if "does not exist" in m or "enoent" in m:
        return "NOT_FOUND"
    if "timeout" in m:
        return "RUNTIME_ERROR"
    # Default catch-all for unexpected exceptions
    return "RUNTIME_ERROR"


def _get_env_required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        pytest.skip(f"Required env var {name} not set")
    return val


def _hash_name(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def tool_module():
    mod_name = os.environ.get("PY_TOOL_MODULE", "tool_under_test")
    try:
        return importlib.import_module(mod_name)
    except Exception as e:
        pytest.fail(f"Failed to import module {mod_name}: {e}")


@pytest.fixture(scope="session")
def tool_entry(tool_module):
    symbol = os.environ.get("PY_TOOL_SYMBOL")
    if symbol:
        if not hasattr(tool_module, symbol):
            pytest.fail(f"PY_TOOL_SYMBOL '{symbol}' not found in module")
        return getattr(tool_module, symbol)

    # Discover a class/object with a callable/awaitable `execute_tool`
    candidates = []
    for name, obj in vars(tool_module).items():
        try:
            if inspect.isclass(obj) and hasattr(obj, "execute_tool"):
                candidates.append(obj)
        except Exception:
            continue
    for name, obj in vars(tool_module).items():
        if not inspect.isclass(obj) and hasattr(obj, "execute_tool"):
            candidates.append(obj)
    if not candidates:
        pytest.fail("No suitable tool entry (with execute_tool) found in module")
    # Prefer a class named like MemoryRead
    for obj in candidates:
        if isinstance(obj, type) and "memory" in obj.__name__.lower() and "read" in obj.__name__.lower():
            return obj
    return candidates[0]


@pytest.fixture(scope="function")
def tool_instance(tool_entry):
    namespace = _get_env_required("K8S_NAMESPACE")
    pod_name = _get_env_required("K8S_POD_NAME")
    config = {"namespace": namespace, "pod_name": pod_name}
    # If class: construct with config; if object: return as-is
    if isinstance(tool_entry, type):
        try:
            return tool_entry(config)
        except TypeError:
            # Some tools might accept no args; try bare init
            return tool_entry()
    return tool_entry


@pytest.fixture(scope="function")
def k8s_exec(tool_instance):
    """
    Execute a shell command inside the configured pod.
    Tries the tool's own Kubernetes manager if accessible, otherwise falls back to kubectl.
    Returns (stdout_str, rc_int).
    """
    namespace = _get_env_required("K8S_NAMESPACE")
    pod_name = _get_env_required("K8S_POD_NAME")

    # Try using the tool's own manager if available
    mgr = None
    if hasattr(tool_instance, "_mgr") and callable(getattr(tool_instance, "_mgr")):
        try:
            mgr = tool_instance._mgr()  # type: ignore[attr-defined]
        except Exception:
            mgr = None

    def run(cmd: str) -> Tuple[str, int]:
        if mgr is not None and hasattr(mgr, "execute_command"):
            try:
                # Tool's manager API: (stdout, rc)
                stdout, rc = mgr.execute_command(pod_name, f"sh -lc {shlex.quote(cmd)}")  # type: ignore[call-arg]
                return (str(stdout), int(rc))
            except Exception:
                pass
        # Fallback to kubectl
        full_cmd = [
            "kubectl",
            "exec",
            "-n",
            namespace,
            pod_name,
            "--",
            "/bin/sh",
            "-lc",
            cmd,
        ]
        proc = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return (proc.stdout, proc.returncode)

    return run


@pytest.fixture(scope="function")
def prepare_memory(tool_instance, k8s_exec):
    """
    Ensure a deterministic set of files exist inside the memory directory.
    """
    mem_dir = getattr(tool_instance, "memory_dir", "/memory")
    # Deterministic contents
    index_text = "Integration test index\nSecond line\n"
    alpha_text = "Alpha test file\nLine 2\n"
    readme_text = "Readme content\n"
    setup_cmd = (
        f'mem={shlex.quote(mem_dir)}; '
        f'mkdir -p "$mem/notes"; '
        f'printf %s {shlex.quote(index_text)} > "$mem/index.md"; '
        f'printf %s {shlex.quote(alpha_text)} > "$mem/notes/alpha.txt"; '
        f'printf %s {shlex.quote(readme_text)} > "$mem/readme.txt"; '
        f'ls -l "$mem" >/dev/null 2>&1'
    )
    stdout, rc = k8s_exec(setup_cmd)
    if rc != 0:
        pytest.skip(f"Failed to prepare memory dir {mem_dir}: rc={rc}, out={stdout}")
    return {"mem_dir": mem_dir, "index_text": index_text, "alpha_text": alpha_text}


# ---------------------------------------------------------------------------
# Core test
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", TEST_PARAMS)
def test_k8s_memory_read(case, tool_instance, k8s_exec, prepare_memory):
    """
    Execute tool against Kubernetes pod and validate behavior.
    """
    mem_dir = prepare_memory["mem_dir"]
    index_text = prepare_memory["index_text"]
    alpha_text = prepare_memory["alpha_text"]

    async def run_case() -> Dict[str, Any]:
        # Instance lifecycle if exposed by the tool
        instance_id = "itest-" + _hash_name(case["case_name"])
        if hasattr(tool_instance, "_initialize_instance") and inspect.iscoroutinefunction(getattr(tool_instance, "_initialize_instance")):
            await tool_instance._initialize_instance(instance_id)  # type: ignore[attr-defined]
        try:
            exec_coro = getattr(tool_instance, "execute_tool")
            assert callable(exec_coro), "Tool missing execute_tool"
            # The tool's API is async; run with timeout
            result_obj = await asyncio.wait_for(exec_coro(instance_id, dict(case["parameters"])), timeout=30.0)
        finally:
            if hasattr(tool_instance, "_cleanup_instance") and inspect.iscoroutinefunction(getattr(tool_instance, "_cleanup_instance")):
                try:
                    await tool_instance._cleanup_instance(instance_id)  # type: ignore[attr-defined]
                except Exception:
                    pass
        return _to_dictish(result_obj)

    # Run the tool
    try:
        out = asyncio.run(run_case())
    except Exception as e:
        # Treat unexpected exceptions as runtime errors
        out = {"success": False, "error": f"Unexpected test runner exception: {e}"}

    success = bool(out.get("success"))
    result_raw = out.get("result")
    error_raw = out.get("error")
    error_msg = ""
    if isinstance(error_raw, dict):
        error_msg = error_raw.get("message") or error_raw.get("error") or str(error_raw)
    else:
        error_msg = str(error_raw) if error_raw is not None else ""

    # Canonicalize
    normalized_result = _normalize_success_result(result_raw) if success else None
    error_obj = (
        {"code": _map_error_code(error_msg), "message": (error_msg or "")[:128]}
        if not success
        else None
    )

    # Record outcome BEFORE assertions to ensure summary always emits
    RESULTS.append(
        {
            "case_name": case["case_name"],
            "success": success,
            "result": normalized_result,
            "error": error_obj,
        }
    )

    # -------------------- Assertions per category --------------------
    category = case["category"]

    if category == "Happy Path":
        assert success is True, f"Expected success, got error: {error_msg}"

        # Validate core fields
        assert normalized_result and "content" in normalized_result, "Missing content in result"
        assert normalized_result["content"]["text_prefix"] != "", "Empty content prefix"

        # Physical verification in pod:
        if case["case_name"] == "list_directory_default":
            ls_cmd = f'find {shlex.quote(mem_dir)} -type f -print | sort'
            stdout, rc = k8s_exec(ls_cmd)
            assert rc == 0, f"find failed rc={rc}"
            # Ensure key files are present
            assert f"{mem_dir}/index.md" in stdout
            assert f"{mem_dir}/notes/alpha.txt" in stdout
            # Returned content must reference these files
            content = result_raw.get("content") if isinstance(result_raw, dict) else ""
            assert f"- {mem_dir}/index.md" in content
            assert f"- {mem_dir}/notes/alpha.txt" in content

        elif case["case_name"] == "read_alpha_txt":
            cat_cmd = f'cat {shlex.quote(mem_dir)}/notes/alpha.txt'
            stdout, rc = k8s_exec(cat_cmd)
            assert rc == 0, f"cat alpha.txt failed rc={rc}"
            tool_content = result_raw.get("content") if isinstance(result_raw, dict) else ""
            assert tool_content == stdout, "Tool content mismatch with pod file (alpha.txt)"
            assert tool_content == alpha_text, "Alpha content not as prepared"

        elif case["case_name"] == "read_index_md":
            cat_cmd = f'cat {shlex.quote(mem_dir)}/index.md'
            stdout, rc = k8s_exec(cat_cmd)
            assert rc == 0, f"cat index.md failed rc={rc}"
            tool_content = result_raw.get("content") if isinstance(result_raw, dict) else ""
            assert tool_content == stdout, "Tool content mismatch with pod file (index.md)"
            assert tool_content == index_text, "Index content not as prepared"

    elif category in ("Sad Path", "Security"):
        assert success is False, "Expected failure for negative/security case"
        assert error_obj is not None and error_obj["message"] != "", "Error object should be present"

        # Expected error class
        expected_code = None
        if category == "Sad Path":
            if "wrong_type" in case["case_name"]:
                expected_code = "VALIDATION_ERROR"
            else:
                expected_code = "NOT_FOUND"
        else:  # Security
            expected_code = "SECURITY_BLOCKED"
        assert error_obj["code"] == expected_code, f"Got {error_obj['code']} expected {expected_code}"


# ---------------------------------------------------------------------------
# One-line JSON summary output
# ---------------------------------------------------------------------------
def pytest_sessionfinish(session, exitstatus):
    # Emit exactly one JSON line (or write to RESULT_PATH)
    payload = json.dumps(RESULTS, ensure_ascii=False)
    result_path = os.environ.get("RESULT_PATH")
    if result_path:
        try:
            with open(result_path, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:
            # As a last resort, still print the line to stdout
            print(payload)
    else:
        print(payload)
