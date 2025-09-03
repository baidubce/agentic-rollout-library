import os
import json
from typing import Any, Dict

import pytest
import pytest_asyncio
from kodo import KubernetesManager
from workers.tools.cc_tools.bash_tool.k8s_bash_tool import K8sBashTool

TEST_CASES = [
    {"case_name": "Happy Path: List files in the current directory", "category": "Happy Path", "parameters": {"command": "ls -la"}},
    {"case_name": "Happy Path: Create a directory and then list contents", "category": "Happy Path", "parameters": {"command": "mkdir new_dir && ls -l"}},
    {"case_name": "Happy Path: Execute a command with a custom timeout", "category": "Happy Path", "parameters": {"command": "echo 'Process with short timeout' && sleep 1", "timeout": 8000}},
    {"case_name": "Happy Path: Generate both stdout and stderr", "category": "Happy Path", "parameters": {"command": "ls -l /etc /nonexistent_directory"}},
    {"case_name": "Sad Path: Missing required 'command' parameter", "category": "Sad Path", "parameters": {"timeout": 5000}},
    {"case_name": "Sad Path: Wrong parameter type for 'command'", "category": "Sad Path", "parameters": {"command": 12345}},
    {"case_name": "Sad Path: Wrong parameter type for 'timeout'", "category": "Sad Path", "parameters": {"command": "ls", "timeout": "five_seconds"}},
    {"case_name": "Sad Path: Empty string for 'command'", "category": "Sad Path", "parameters": {"command": ""}},
    {"case_name": "Sad Path: Command that is syntactically invalid", "category": "Sad Path", "parameters": {"command": "&& ls"}},
    {"case_name": "Sad Path: Non-existent command", "category": "Sad Path", "parameters": {"command": "this_command_does_not_exist"}},
    {"case_name": "Security: Attempt to use a banned command like 'rm'", "category": "Security", "parameters": {"command": "rm -rf /"}},
    {"case_name": "Security: Attempt path traversal with 'cd ../../'", "category": "Security", "parameters": {"command": "cd ../../../etc"}},
    {"case_name": "Security: Attempt command injection via chained banned command", "category": "Security", "parameters": {"command": "echo 'hello'; sudo apt-get update"}},
]


def _trim(s: Any, maxlen: int = 2000) -> Any:
    if s is None:
        return None
    if not isinstance(s, str):
        return s
    if len(s) <= maxlen:
        return s
    return s[:maxlen] + f"\n...[truncated {len(s) - maxlen} chars]"


def _dbg_dump(case: Dict[str, Any], env: Dict[str, Any], params: Dict[str, Any], res) -> None:
    result = res.result or {}
    metrics = res.metrics or {}
    payload = {
        "case_name": case.get("case_name"),
        "category": case.get("category"),
        "env": {
            "namespace": env.get("namespace"),
            "pod_name": env.get("pod_name"),
            "kubeconfig_path": env.get("kubeconfig_path"),
        },
        "input_params": params,
        "response": {
            "success": res.success,
            "error": res.error,
            "metrics": metrics,
            "result_meta": {
                "return_code": result.get("return_code"),
                "command": result.get("command"),
                "cwd": result.get("cwd"),
                "pod_name": result.get("pod_name"),
                "namespace": result.get("namespace"),
                "stdout_length": (len(result.get("stdout")) if isinstance(result.get("stdout"), str) else None),
                "stderr_length": (len(result.get("stderr")) if isinstance(result.get("stderr"), str) else None),
                "interrupted": result.get("interrupted"),
                "timeout_ms": result.get("timeout_ms"),
                "execution_location": metrics.get("execution_location") if isinstance(metrics, dict) else None,
            },
            "stdout_preview": _trim(result.get("stdout"), 2000),
            "stderr_preview": _trim(result.get("stderr"), 2000),
        },
    }
    print("=== DEBUG START ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("=== DEBUG END ===")


@pytest.fixture(scope="session")
def k8s_env() -> Dict[str, Any]:
    return {
        "namespace": os.environ.get("K8S_NAMESPACE", "default"),
        "pod_name": os.environ.get("K8S_POD_NAME", "target-pod"),
        "kubeconfig_path": os.environ.get("KUBECONFIG"),
    }


@pytest.fixture(scope="session")
def k8s_manager(k8s_env):
    return KubernetesManager(
        namespace=k8s_env["namespace"],
        kubeconfig_path=k8s_env["kubeconfig_path"],
    )


@pytest.fixture(scope="session", autouse=True)
def ensure_workspace(k8s_manager, k8s_env):
    try:
        k8s_manager.execute_command(k8s_env["pod_name"], "mkdir -p /workspace")
    except Exception as e:
        print(f"[DBG][ensure_workspace] mkdir -p /workspace failed: {e}")
    try:
        out, code = k8s_manager.execute_command(k8s_env["pod_name"], "sh -lc 'echo SHELL_OK && pwd'")
        print(f"[DBG][shell_probe] rc={code} out={_trim(out, 400)}")
    except Exception as e:
        print(f"[DBG][shell_probe] failed: {e}")


@pytest_asyncio.fixture
async def tool(k8s_env, ensure_workspace):
    cfg = {
        "pod_name": k8s_env["pod_name"],
        "namespace": k8s_env["namespace"],
        "kubeconfig_path": k8s_env["kubeconfig_path"],
        "original_workdir": "/workspace",
        "timeout_ms": 120000,
    }
    t = K8sBashTool(cfg)
    instance_id = await t.create_instance()
    try:
        yield t, instance_id
    finally:
        await t._cleanup_instance(instance_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", TEST_CASES, ids=[c["case_name"] for c in TEST_CASES])
async def test_k8s_bash_tool_cases(tool, case, k8s_env):
    t, instance_id = tool
    params = dict(case["parameters"])
    if "timeout" in params and "timeout_ms" not in params:
        params["timeout_ms"] = params.pop("timeout")

    res = await t.execute_tool(instance_id, params)

    success = res.success
    error = res.error
    result = res.result or {}
    metrics = res.metrics or {}

    print(
        f"[DBG][summary] case='{case['case_name']}' category='{case['category']}' "
        f"success={success} rc={result.get('return_code')} "
        f"cmd={repr(result.get('command'))} cwd={repr(result.get('cwd'))}"
    )

    if not success or case["category"] == "Happy Path":
        _dbg_dump(case, k8s_env, params, res)

    assert isinstance(success, bool)
    assert isinstance(result, dict)
    assert isinstance(metrics, dict)

    cat = case["category"]

    if cat == "Happy Path":
        if "nonexistent_directory" in params.get("command", ""):
            assert success is False
            assert "Exit code" in (result.get("stderr") or "")
            assert (result.get("stdout") or "") != ""
        else:
            assert success is True
            assert "stdout" in result and isinstance(result["stdout"], str)
            assert "stdout_lines" in result and "stderr_lines" in result
            assert "execution_location" in metrics and "return_code" in metrics

    elif cat == "Sad Path":
        cmd = params.get("command")
        if cmd is None:
            assert success is False and error
        elif cmd == "":
            assert success is False
        elif cmd == "&& ls":
            assert success is False
        elif isinstance(cmd, int):
            assert success is False
        elif params.get("timeout_ms") == "five_seconds":
            assert success is False
        else:
            assert success is False

    elif cat == "Security":
        assert success is False
        assert error and any(k in error.lower() for k in ["blocked", "dangerous", "not allowed", "security"])

    if result:
        assert "stdout" in result and "stderr" in result and "interrupted" in result
    else:
        assert error
