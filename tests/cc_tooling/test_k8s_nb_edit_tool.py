import asyncio
import base64
import hashlib
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Tuple

import pytest

import random

random.seed(1337)
os.environ.setdefault("PYTHONHASHSEED", "1337")


TEST_CASES: List[Dict[str, Any]] = [
    # -------------------- Happy Path --------------------
    {
        "case_name": "replace_default_edit_mode_at_index_1",
        "category": "Happy Path",
        "parameters": {
            "notebook_path": "<CASE_PATH>",
            "cell_number": 1,
            "new_source": "print('replaced')",
            # edit_mode omitted -> defaults to 'replace'
        },
    },
    {
        "case_name": "insert_markdown_cell_at_start",
        "category": "Happy Path",
        "parameters": {
            "notebook_path": "<CASE_PATH>",
            "cell_number": 0,
            "new_source": "## inserted markdown",
            "cell_type": "markdown",
            "edit_mode": "insert",
        },
    },
    {
        "case_name": "append_code_cell_at_end",
        "category": "Happy Path",
        "parameters": {
            "notebook_path": "<CASE_PATH>",
            "cell_number": 2,  # seed has 2 cells; inserting at len(cells) appends
            "new_source": "print('appended')",
            "cell_type": "code",
            "edit_mode": "insert",
        },
    },
    {
        "case_name": "delete_first_cell",
        "category": "Happy Path",
        "parameters": {
            "notebook_path": "<CASE_PATH>",
            "cell_number": 0,
            "new_source": "",  # required by schema even if ignored on delete
            "edit_mode": "delete",
        },
    },
    # -------------------- Sad Path ----------------------
    {
        "case_name": "path_is_not_absolute",
        "category": "Sad Path",
        "parameters": {
            "notebook_path": "tmp/relative.ipynb",
            "cell_number": 0,
            "new_source": "x",
        },
    },
    {
        "case_name": "wrong_file_extension",
        "category": "Sad Path",
        "parameters": {
            "notebook_path": "<NON_IPYNB_PATH>",
            "cell_number": 0,
            "new_source": "x",
        },
    },
    {
        "case_name": "negative_cell_index",
        "category": "Sad Path",
        "parameters": {
            "notebook_path": "<CASE_PATH>",
            "cell_number": -1,
            "new_source": "x",
        },
    },
    {
        "case_name": "out_of_bounds_replace",
        "category": "Sad Path",
        "parameters": {
            "notebook_path": "<CASE_PATH>",
            "cell_number": 99,
            "new_source": "x",
            "edit_mode": "replace",
        },
    },
    {
        "case_name": "insert_missing_cell_type",
        "category": "Sad Path",
        "parameters": {
            "notebook_path": "<CASE_PATH>",
            "cell_number": 0,
            "new_source": "x",
            "edit_mode": "insert",
            # cell_type intentionally omitted
        },
    },
    {
        "case_name": "notebook_is_invalid_json",
        "category": "Sad Path",
        "parameters": {
            "notebook_path": "<BAD_JSON_PATH>",
            "cell_number": 0,
            "new_source": "x",
        },
    },
    # -------------------- Security ----------------------
    {
        "case_name": "command_injection_in_edit_mode",
        "category": "Security",
        "parameters": {
            "notebook_path": "<CASE_PATH>",
            "cell_number": 0,
            "new_source": "x",
            "edit_mode": "delete; rm -rf /",
        },
    },
    {
        "case_name": "semicolon_in_path",
        "category": "Security",
        "parameters": {
            "notebook_path": "<INJECT_PATH>",
            "cell_number": 0,
            "new_source": "x",
        },
    },
]

# Build pytest.param entries with marks for categories
def _mkparam(case: Dict[str, Any]):
    cat = case["category"].lower().split()[0]
    mark = {
        "happy": pytest.mark.happy,
        "sad": pytest.mark.sad,
        "security": pytest.mark.security,
    }.get(cat, pytest.mark.other)
    return pytest.param(case, marks=mark, id=case["case_name"])


PARAM_CASES = [_mkparam(c) for c in TEST_CASES]

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

RESULTS: List[Dict[str, Any]] = []  # session-scoped summary container

def sha1_8(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]

def snake_keys(x: Any) -> Any:
    """Recursively convert dict keys to snake_case."""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            kk = re.sub(r"[-\s]+", "_", k)
            kk = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", kk).lower()
            out[kk] = snake_keys(v)
        return out
    if isinstance(x, list):
        return [snake_keys(v) for v in x]
    return x

def pick_error_code(msg: str) -> str:
    m = msg.lower()
    if "does not exist" in m or "no such file" in m:
        return "NOT_FOUND"
    if any(s in m for s in [
        "must be absolute",
        "file must be a jupyter notebook",
        "non-negative",
        "valid json",
        "edit mode must be",
        "required when using edit_mode=insert",
        "out of bounds",
        "additionalproperties",  # generic schema-ish
    ]):
        return "VALIDATION_ERROR"
    if "timeout" in m:
        return "RUNTIME_ERROR"
    return "RUNTIME_ERROR"

def tool_result_to_dict(tr: Any) -> Dict[str, Any]:
    """Extract a dict {success, result, error, metrics} from arbitrary ToolResult-like objects."""
    if isinstance(tr, dict):
        d = dict(tr)
    else:
        # Try attributes commonly present
        d = {}
        for key in ("success", "result", "error", "metrics"):
            if hasattr(tr, key):
                d[key] = getattr(tr, key)
    # Ensure keys exist
    d.setdefault("success", False)
    d.setdefault("result", None)
    d.setdefault("error", None)
    return d

def logical_success(tr_dict: Dict[str, Any]) -> bool:
    """Define semantic success as 'no error payload anywhere'."""
    if tr_dict.get("error"):
        return False
    res = tr_dict.get("result") or {}
    if isinstance(res, dict) and res.get("error"):
        return False
    return bool(tr_dict.get("success", False))

def stable_case_artifact_path(case_name: str) -> str:
    return f"/tmp/nb_{sha1_8(case_name)}.ipynb"

def make_seed_notebook() -> Dict[str, Any]:
    return {
        "cells": [
            {"cell_type": "markdown", "source": "# Title", "metadata": {}},
            {"cell_type": "code", "source": "print('hello')", "metadata": {}, "outputs": []},
        ],
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }

def b64_of_json(data: Dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii")

def pytest_configure(config):
    config.addinivalue_line("markers", "happy: happy-path tests")
    config.addinivalue_line("markers", "sad: negative-path tests")
    config.addinivalue_line("markers", "security: input-hardening tests")

# ---------------------------------------------------------------------------
# Fixtures: environment, module import, tool discovery/instance, k8s exec
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def k8s_env():
    ns = os.environ.get("K8S_NAMESPACE")
    pod = os.environ.get("K8S_POD_NAME")
    if not ns or not pod:
        pytest.skip("K8S_NAMESPACE and K8S_POD_NAME must be set")
    return {"namespace": ns, "pod": pod}

@pytest.fixture(scope="session")
def tool_module():
    mod_path = os.environ.get("PY_TOOL_MODULE", "tool_under_test")
    try:
        return importlib.import_module(mod_path)
    except Exception as e:
        pytest.fail(f"Failed to import module {mod_path}: {e}")

@pytest.fixture(scope="session")
def tool_entry(tool_module):
    sym = os.environ.get("PY_TOOL_SYMBOL")
    candidate = None
    if sym:
        candidate = getattr(tool_module, sym, None)
        if candidate is None:
            pytest.fail(f"PY_TOOL_SYMBOL '{sym}' not found in module.")
    else:
        # Discover: class or object with callable 'execute_tool'
        for name, obj in vars(tool_module).items():
            exec_attr = getattr(obj, "execute_tool", None)
            if exec_attr and (inspect.iscoroutinefunction(exec_attr) or callable(exec_attr)):
                candidate = obj
                break
    if candidate is None:
        pytest.fail("No tool entry found with an 'execute_tool' attribute.")
    return candidate

@pytest.fixture(scope="session")
def tool_instance(tool_entry, k8s_env):
    # If a class, instantiate; if already an instance, use as-is.
    cfg = {
        "namespace": k8s_env["namespace"],
        "pod_name": k8s_env["pod"],
        "timeout": float(os.environ.get("PY_TOOL_TIMEOUT", "30")),
    }
    if inspect.isclass(tool_entry):
        try:
            inst = tool_entry(config=cfg)
        except TypeError:
            # Fallback: try no-arg, then maybe set attributes if available
            inst = tool_entry()
            for k, v in cfg.items():
                if hasattr(inst, k):
                    setattr(inst, k, v)
        return inst
    # If it's a module-level singleton object, try configuring if supported
    inst = tool_entry
    for k, v in cfg.items():
        if hasattr(inst, k):
            setattr(inst, k, v)
    return inst

@pytest.fixture(scope="session")
def k8s_exec(tool_instance, k8s_env):
    """
    Returns a function: run(cmd: str) -> Tuple[str, int]
    Preference: use the tool's own in-pod exec if exposed; else fall back to kubectl.
    """
    async_exec = getattr(tool_instance, "_run_in_pod", None)

    if async_exec and inspect.iscoroutinefunction(async_exec):
        def _runner(cmd: str) -> Tuple[str, int]:
            return asyncio.run(async_exec(cmd))
        return _runner

    def _kubectl_exec(cmd: str) -> Tuple[str, int]:
        full = [
            "kubectl",
            "exec",
            "-n",
            k8s_env["namespace"],
            k8s_env["pod"],
            "--",
            "sh",
            "-lc",
            cmd,
        ]
        proc = subprocess.run(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return proc.stdout, int(proc.returncode)

    return _kubectl_exec

@pytest.fixture(scope="session")
def seed_paths(k8s_exec):
    """Seed a canonical notebook and a bad-JSON file inside the pod."""
    seed_nb = make_seed_notebook()
    seed_b64 = b64_of_json(seed_nb)
    seed_path = f"/tmp/nb_seed_{sha1_8('seed')}.ipynb"
    k8s_exec(f'echo {shlex_quote(seed_b64)} | base64 -d > {shlex_quote(seed_path)}')

    bad_b64 = base64.b64encode(b"this is not json").decode("ascii")
    bad_path = f"/tmp/nb_bad_{sha1_8('seed')}.ipynb"
    k8s_exec(f'echo {shlex_quote(bad_b64)} | base64 -d > {shlex_quote(bad_path)}')

    return {"seed_path": seed_path, "bad_path": bad_path, "seed_json": seed_nb}

# ---------------------------------------------------------------------------
# Helper for shell quoting (stdlib only)
# ---------------------------------------------------------------------------

def shlex_quote(s: str) -> str:
    # Minimal POSIX-safe quoting (avoid importing shlex to keep stdlib-only is fine, but we can use shlex)
    import shlex as _shlex
    return _shlex.quote(s)

# ---------------------------------------------------------------------------
# Core test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", PARAM_CASES)
def test_k8s_tool(case, tool_instance, k8s_env, k8s_exec, seed_paths):
    # Resolve placeholders and prepare per-case artifact
    case_name = case["case_name"]
    expected_success = case["category"] == "Happy Path"
    art_path = stable_case_artifact_path(case_name)
    non_ipynb_path = art_path[:-6] + ".json"  # swap .ipynb -> .json safely when needed
    inject_path = art_path + "; rm -rf /"

    # For test isolation: reset from seed for cases that operate on a valid notebook
    needs_valid_nb = any(
        tok in json.dumps(case["parameters"])
        for tok in ["<CASE_PATH>"]
    )
    if needs_valid_nb:
        # Copy seed -> per-case artifact
        k8s_exec(f"cp {shlex_quote(seed_paths['seed_path'])} {shlex_quote(art_path)}")

    # Prepare a working copy of parameters
    params = json.loads(json.dumps(case["parameters"]))  # deep copy
    replace_tokens = {
        "<CASE_PATH>": art_path,
        "<NON_IPYNB_PATH>": non_ipynb_path,
        "<BAD_JSON_PATH>": seed_paths["bad_path"],
        "<INJECT_PATH>": inject_path,
    }

    def _subst(v):
        if isinstance(v, str):
            for k, repl in replace_tokens.items():
                v = v.replace(k, repl)
        return v

    for k in list(params.keys()):
        params[k] = _subst(params[k])

    # Importantly, ensure required fields exist (TSX requires new_source even for delete)
    assert "notebook_path" in params and "cell_number" in params and "new_source" in params

    # Execute tool
    execute = getattr(tool_instance, "execute_tool", None)
    assert execute is not None and callable(execute), "Tool has no callable execute_tool"

    # Instance id derived from case hash to avoid collisions
    instance_id = f"case-{sha1_8(case_name)}"

    async def _run():
        if inspect.iscoroutinefunction(execute):
            # Signature could be (instance_id, parameters) or just (parameters)
            sig = inspect.signature(execute)
            if len(sig.parameters) >= 2:
                return await execute(instance_id, params)
            else:
                return await execute(params)  # type: ignore[func-returns-value]
        # Fallback (unlikely)
        return execute(instance_id, params)

    tr = asyncio.run(_run())
    trd = tool_result_to_dict(tr)
    norm_result = snake_keys(trd.get("result") or {})
    # Determine "semantic" success
    sem_success = logical_success(trd)

    # ---- Canonicalize error ----
    err_msg = ""
    if trd.get("error"):
        err_msg = str(trd["error"])
    elif isinstance(norm_result, dict) and norm_result.get("error"):
        err_msg = str(norm_result["error"])
    error_obj = None
    if err_msg:
        error_obj = {"code": pick_error_code(err_msg), "message": err_msg.splitlines()[0][:200]}

    # ---- Happy-path assertions & physical verification ----
    if expected_success:
        assert sem_success is True, f"Expected success; got error: {error_obj}"
        # Minimal structural checks
        for key in ["cell_number", "new_source", "cell_type", "language", "edit_mode"]:
            assert key in norm_result, f"Missing key in result: {key}"
        # Physical verification in pod by reading the file
        nb_b64, rc = k8s_exec(f'cat {shlex_quote(params["notebook_path"])} | base64')
        assert rc == 0, f"cat failed with rc={rc}"
        nb = json.loads(base64.b64decode(nb_b64.encode("ascii")).decode("utf-8", errors="replace"))
        cells = nb.get("cells", [])
        # Validate per-branch behavior
        mode = params.get("edit_mode", "replace")
        idx = int(params["cell_number"])
        if mode == "replace":
            tgt = cells[idx]
            assert tgt["source"] == params["new_source"]
            assert tgt.get("execution_count") in (None, 0)  # tool sets None
            assert tgt.get("outputs") == []
        elif mode == "insert":
            assert 0 <= idx <= len(cells)
            inserted = cells[idx]
            assert inserted["source"] == params["new_source"]
            if params.get("cell_type") == "markdown":
                assert inserted["cell_type"] == "markdown"
                assert "outputs" not in inserted
            else:
                assert inserted["cell_type"] == "code"
                assert inserted.get("outputs") == []
        elif mode == "delete":
            # After deleting index 0, the first cell should be the original second cell
            seed = seed_paths["seed_json"]
            assert len(cells) == len(seed["cells"]) - 1
            # Spot check: first remaining cell equals original index 1
            assert cells[0]["source"] == seed["cells"][1]["source"]
    else:
        # ---- Sad/Security assertions ----
        # Expected a failure: either tool has success=False or an error message present
        # Standardize as "semantic failure"
        assert sem_success is False, "Expected a failure, but tool reported success with no error"
        assert error_obj is not None and error_obj["message"], "Expected a descriptive error"

    # ---- Record result for single-line summary ----
    RESULTS.append(
        {
            "case_name": case_name,
            "success": sem_success,
            "result": norm_result if sem_success else None,
            "error": error_obj if not sem_success else None,
        }
    )

# ---------------------------------------------------------------------------
# Session finish hook: emit single JSON line or write to RESULT_PATH
# ---------------------------------------------------------------------------

def pytest_sessionfinish(session, exitstatus):
    try:
        out = json.dumps(RESULTS, ensure_ascii=False)
        result_path = os.environ.get("RESULT_PATH")
        if result_path:
            with open(result_path, "w", encoding="utf-8") as f:
                f.write(out)
        else:
            # Print exactly one line
            print(out)
    except Exception as e:
        # Last-resort single-line error output (still one line)
        print(json.dumps({"error": f"failed_to_emit_results: {e}"}))
