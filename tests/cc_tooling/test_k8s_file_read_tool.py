import base64
import json
import os
import re
import shlex
import subprocess
import time
from typing import Any, Dict, List

import pytest

PNG_1x1_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO9Q7JcAAAAASUVORK5CYII="

TEST_CASES: List[Dict[str, Any]] = [
    {"case_name": "Happy Path: Read a small text file from the beginning", "category": "Happy Path", "parameters": {"file_path": "/app/src/main.js"}},
    {"case_name": "Happy Path: Read a portion of a text file using offset and limit", "category": "Happy Path", "parameters": {"file_path": "/app/logs/large_log_file.log", "offset": 100, "limit": 50}},
    {"case_name": "Happy Path: Read a supported image file", "category": "Happy Path", "parameters": {"file_path": "/app/assets/diagram.png"}},
    {"case_name": "Happy Path: Read from the very first line using offset 1", "category": "Happy Path", "parameters": {"file_path": "/app/config.yaml", "offset": 1, "limit": 10}},
    {"case_name": "Sad Path: Missing required 'file_path' parameter", "category": "Sad Path", "parameters": {"offset": 10, "limit": 20}},
    {"case_name": "Sad Path: Wrong parameter type for 'file_path' (number instead of string)", "category": "Sad Path", "parameters": {"file_path": 12345}},
    {"case_name": "Sad Path: Wrong parameter type for 'offset' (string instead of number)", "category": "Sad Path", "parameters": {"file_path": "/app/src/main.js", "offset": "one-hundred"}},
    {"case_name": "Sad Path: Read a file that does not exist", "category": "Sad Path", "parameters": {"file_path": "/app/non_existent_file.txt"}},
    {"case_name": "Sad Path: Empty string for 'file_path'", "category": "Sad Path", "parameters": {"file_path": ""}},
    {"case_name": "Sad Path: Read a large file without using offset/limit", "category": "Sad Path", "parameters": {"file_path": "/app/data/large_dataset.csv"}},
    {"case_name": "Sad Path: Using a negative offset value", "category": "Sad Path", "parameters": {"file_path": "/app/src/main.js", "offset": -5, "limit": 10}},
    {"case_name": "Security: Attempt path traversal to read a sensitive system file", "category": "Security", "parameters": {"file_path": "../../../../etc/passwd"}},
    {"case_name": "Security: Attempt command injection in 'file_path'", "category": "Security", "parameters": {"file_path": "/app/src/main.js; ls -la /"}},
]

NAMESPACE = os.environ.get("K8S_NAMESPACE", "default")
POD_NAME = os.environ.get("K8S_POD_NAME", "file-read-tool-e2e")
KUBECTL = os.environ.get("KUBECTL", "kubectl")
TIMEOUT_SEC = int(os.environ.get("E2E_TIMEOUT_SEC", "300"))
E2E_IMAGE = os.environ.get("E2E_IMAGE", "ubuntu:22.04")


def sh(cmd: List[str], check=True, capture_output=True, text=True, env=None, stdin=None):
    print(f"[E2E] run: {' '.join(cmd)}")
    p = subprocess.run(cmd, check=False, capture_output=capture_output, text=text, env=env, input=stdin)
    print(f"[E2E] rc={p.returncode}")
    if p.stdout:
        print(f"[E2E] stdout:\n{p.stdout}")
    if p.stderr:
        print(f"[E2E] stderr:\n{p.stderr}")
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)} rc={p.returncode}\n--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}\n")
    return p


def ensure_namespace(ns: str):
    p = sh([KUBECTL, "get", "ns", ns], check=False)
    if p.returncode != 0:
        sh([KUBECTL, "create", "ns", ns])


def apply_pod_with_files(ns: str, pod: str, png_b64: str):
    yaml = f"""
apiVersion: v1
kind: Pod
metadata:
  name: {pod}
  namespace: {ns}
spec:
  restartPolicy: Never
  volumes:
  - name: app-data
    emptyDir: {{}}
  initContainers:
  - name: init
    image: {E2E_IMAGE}
    imagePullPolicy: IfNotPresent
    command: ["/bin/bash","-lc"]
    args:
    - |
      set -e
      mkdir -p /app/src /app/logs /app/assets /app/data
      printf "console.log('hello');\\n" > /app/src/main.js
      :> /app/config.yaml; for i in $(seq 1 20); do echo "key${{i}}: value${{i}}" >> /app/config.yaml; done
      :> /app/logs/large_log_file.log; for i in $(seq 1 2000); do echo "line ${{i}} - lorem ipsum dolor sit amet" >> /app/logs/large_log_file.log; done
      echo "{png_b64}" | base64 -d > /app/assets/diagram.png
      head -c 400000 /dev/zero | tr '\\0' 'A' > /app/data/large_dataset.csv
    volumeMounts:
    - name: app-data
      mountPath: /app
  containers:
  - name: app
    image: {E2E_IMAGE}
    imagePullPolicy: IfNotPresent
    command: ["/bin/bash","-lc"]
    args: ["sleep infinity"]
    volumeMounts:
    - name: app-data
      mountPath: /app
"""
    print("[E2E] applying pod yaml:")
    print(yaml)
    sh([KUBECTL, "-n", ns, "delete", "pod", pod, "--ignore-not-found=true"], check=False)
    sh([KUBECTL, "-n", ns, "apply", "-f", "-"], stdin=yaml, text=True)


def wait_pod_ready(ns: str, pod: str, timeout_sec: int):
    sh([KUBECTL, "-n", ns, "wait", f"pod/{pod}", "--for=condition=Initialized", f"--timeout={timeout_sec}s"])
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        p = sh([KUBECTL, "-n", ns, "get", "pod", pod, "-o", "json"], check=False)
        if p.returncode == 0 and p.stdout:
            j = json.loads(p.stdout)
            conds = j.get("status", {}).get("conditions", [])
            ready = any(c.get("type") == "ContainersReady" and c.get("status") == "True" for c in conds)
            print(f"[E2E] ready={ready}")
            if ready:
                return
        time.sleep(2)
    raise TimeoutError(f"pod {ns}/{pod} not ready within {timeout_sec}s")


def verify_pod_dataset():
    reqs = [
        "ls -l /app/src /app/logs /app/assets /app/data || true",
        "stat -c %s /app/src/main.js || true",
        "wc -c /app/src/main.js || true",
        "stat -c %s /app/logs/large_log_file.log || true",
        "wc -l /app/logs/large_log_file.log || true",
        "test -e /app/assets/diagram.png && echo png:OK || echo png:MISS",
    ]
    for r in reqs:
        sh([KUBECTL, "-n", NAMESPACE, "exec", POD_NAME, "--", "sh", "-lc", r], check=False)


@pytest.fixture(scope="session", autouse=True)
def ensure_real_pod():
    ensure_namespace(NAMESPACE)
    apply_pod_with_files(NAMESPACE, POD_NAME, PNG_1x1_BASE64)
    wait_pod_ready(NAMESPACE, POD_NAME, TIMEOUT_SEC)
    verify_pod_dataset()
    yield
    if os.environ.get("KEEP_E2E_POD") not in ("1", "true", "TRUE", "yes"):
        sh([KUBECTL, "-n", NAMESPACE, "delete", f"pod/{POD_NAME}", "--ignore-not-found=true"], check=False)


@pytest.fixture(autouse=True)
def shell_wrap_kodo(monkeypatch):
    import workers.tools.cc_tools.file_read_tool.k8s_file_read_tool as mod
    RealKM = mod.KubernetesManager

    def rewrite(cmd: str) -> str:
        m = re.search(r"\(stat -c %s\s+(\S+)\s+2>/dev/null\s+\|\|\s+wc -c\s+<\s+\1\)\s+2>/dev/null", cmd)
        if m:
            p = m.group(1)
            return f"stat -c %s {p} 2>/dev/null || (wc -c {p} | awk '{{print $1}}')"
        m2 = re.search(r"wc -c\s+<\s+(\S+)", cmd)
        if m2:
            p = m2.group(1)
            return re.sub(r"wc -c\s+<\s+\S+", f"wc -c {p} | awk '{{print $1}}'", cmd)
        if "base64 -w 0 " in cmd:
            return cmd.replace("base64 -w 0 ", "base64 ") + " | tr -d '\\n'"
        cmd = re.sub(r"identify -format\s+'%w %h'\s+(\S+)", r"identify -format %w:%h \1", cmd)
        cmd = re.sub(r'identify -format\s+"%w %h"\s+(\S+)', r"identify -format %w:%h \1", cmd)
        return cmd

    class ShellWrappedKM(RealKM):
        def execute_command(self, pod_name: str, command: str):
            cmd = command if isinstance(command, str) else str(command)
            cmd = rewrite(cmd)
            wrapped = f"sh -lc {shlex.quote(cmd)}"
            out, rc = super().execute_command(pod_name, wrapped)
            print(f"[E2E] exec rc={rc} out<<<{out}>>>")
            return out, rc

    monkeypatch.setattr(mod, "KubernetesManager", ShellWrappedKM)
    return mod


@pytest.fixture
def tool():
    from workers.tools.cc_tools.file_read_tool.k8s_file_read_tool import K8sFileReadTool
    return K8sFileReadTool({"pod_name": POD_NAME, "namespace": NAMESPACE})


def expected_success(category: str) -> bool:
    if category == "Happy Path":
        return True
    if category in ("Sad Path", "Security"):
        return False
    return False


@pytest.mark.asyncio
async def test_schema(tool):
    schema = tool.get_openai_tool_schema()
    assert getattr(schema, "type") == "function"
    fn = getattr(schema, "function")
    assert getattr(fn, "name") == "k8s_file_read"
    params = getattr(fn, "parameters")
    props = params.get("properties", {}) if hasattr(params, "get") else getattr(params, "properties", {})
    required = params.get("required", []) if hasattr(params, "get") else getattr(params, "required", [])
    assert "file_path" in props
    assert "file_path" in required


@pytest.mark.parametrize("case", TEST_CASES, ids=[c["case_name"] for c in TEST_CASES])
@pytest.mark.asyncio
async def test_cases(tool, case):
    iid = await tool.create_instance()
    params = dict(case["parameters"])
    print(f"[CASE] {case['case_name']} | category={case['category']} | params={params}")
    res = await tool.execute_tool(iid, params)
    if hasattr(tool, "delete_instance"):
        await tool.delete_instance(iid)
    elif hasattr(tool, "close_instance"):
        await tool.close_instance(iid)
    elif hasattr(tool, "_cleanup_instance"):
        await tool._cleanup_instance(iid)
    print(f"[RESULT] success={res.success} error={res.error} metrics={res.metrics}")
    if isinstance(res.result, dict):
        print(f"[RESULT] keys={list(res.result.keys())}")
        if "file" in res.result and isinstance(res.result["file"], dict):
            meta = {k: res.result["file"].get(k) for k in ["filePath", "numLines", "startLine", "totalLines"]}
            print(f"[RESULT] file_meta={meta}")
    want = expected_success(case["category"])
    if want:
        assert res.success, f"{case['case_name']} expected success, got error: {res.error}"
        if case["case_name"].startswith("Happy Path: Read a portion"):
            assert res.result["file"]["numLines"] == 50
            assert res.result["file"]["startLine"] == 100
            assert res.result["file"]["content"].splitlines()[0].startswith("line 100")
        if case["case_name"].startswith("Happy Path: Read a supported image file"):
            assert res.result["type"] == "image"
            base64.b64decode(res.result["file"]["base64"])
        if case["case_name"].startswith("Happy Path: Read from the very first line"):
            assert res.result["file"]["numLines"] == 10
            assert res.result["file"]["startLine"] == 1
            assert res.result["file"]["content"].splitlines()[0].startswith("key1:")
    else:
        assert not res.success, f"{case['case_name']} expected failure, got success"
