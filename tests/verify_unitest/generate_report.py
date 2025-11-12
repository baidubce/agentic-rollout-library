#!/usr/bin/env python3
"""
Log Parser for SWE-bench Test Results

This script parses test logs from different repositories (astropy, django, sympy)
and analyzes the pass/fail rates for f2p (fail-to-pass) and p2p (pass-to-pass) tests.
"""

import json
import os
import re
from enum import Enum
from typing import Dict, List, Tuple, Any
from pathlib import Path


class TestStatus(Enum):
    FAILED = "FAILED"
    PASSED = "PASSED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    XFAIL = "XFAIL"
    TIMEOUT = "TIMEOUT"


# class ResolvedStatus(Enum):
#     """SWE-bench评估状态"""
#     FULL = "RESOLVED_FULL"
#     PARTIAL = "RESOLVED_PARTIAL"
#     NO = "RESOLVED_NO"


def extract_test_output_from_log(log_content: str) -> Tuple[str, bool]:
    """
    Extract test output from log content.
    Looks for content between test execution and TEST_OUTPUT_END marker.
    If multiple TEST_OUTPUT blocks exist, uses the LAST one.

    Returns:
        Tuple[str, bool]: (extracted_output, is_timeout)
    """
    # Check for timeout first
    if "Command timed out" in log_content:
        return log_content.strip(), True

    lines = log_content.split('\n')

    # Find ALL end markers and use the LAST one
    end_marker = "{TEST_OUTPUT_END}"
    end_indices = []
    for i, line in enumerate(lines):
        if end_marker in line:
            end_indices.append(i)

    if not end_indices:
        # If no end marker found, take the last part of the log
        end_idx = len(lines) - 1
    else:
        # Use the LAST TEST_OUTPUT_END marker
        end_idx = end_indices[-1]

    # Find the corresponding TEST_OUTPUT_START for this end marker
    start_marker = "{TEST_OUTPUT_START}"
    start_idx = -1

    # Look backwards from end_idx to find the corresponding start marker
    for i in range(end_idx - 1, -1, -1):
        if start_marker in lines[i]:
            start_idx = i
            break

    if start_idx == -1:
        # If no corresponding start marker found, look for test execution patterns
        test_start_patterns = [
            r'^\+ pytest',  # pytest command execution (with + prefix)
            r'pytest.*-rA',  # pytest with -rA flag
            r'test session starts',  # pytest session start
            r'collected \d+ items',  # pytest collection summary
            r'Ran \d+ tests',  # django test summary
            r'PASSED\s+',  # pytest PASSED lines
            r'FAILED\s+',  # pytest FAILED lines
            r'test_.*\.\.\.',  # Django test format: test_name ...
            r'OK$',  # Django success marker
            r'FAILED\s*\(',  # Django failure summary
        ]

        # Start searching from a reasonable point before the end
        search_start = max(0, end_idx - 2000)  # Look back up to 2000 lines

        for i in range(search_start, end_idx):
            line = lines[i].strip()

            # Skip conda activation and setup lines
            if any(pattern in line.lower() for pattern in [
                'conda activate', 'pip install', 'cd /testbed', 'creating table',
                'running migrations', 'requirement already satisfied', 'installing collected packages',
                'successfully installed', 'running command python setup.py'
            ]):
                continue

            if any(re.search(pattern, line) for pattern in test_start_patterns):
                start_idx = i
                break

    if start_idx == -1:
        # If no clear start found, start from a reasonable point before end
        start_idx = max(0, end_idx - 500)  # Look back up to 500 lines

    return '\n'.join(lines[start_idx:end_idx]), False


def parse_log_pytest(log: str) -> Dict[str, str]:
    """Parser for PyTest framework logs"""
    test_status_map = {}
    '''
    # 检查是否包含HTTP错误（包括502 Bad Gateway等）
    if "HTTP Error 502" in log or "Bad Gateway" in log or "urllib.error.HTTPError" in log:
        # 对于包含HTTP错误的日志，将相关测试标记为SKIPPED
        # 尝试解析测试总数
        test_count_match = re.search(r'(?:collected|found)\s+(\d+)\s+(?:tests?|items?)', log)
        if test_count_match:
            test_count = int(test_count_match.group(1))
            for i in range(test_count):
                test_status_map[f"test_skipped_{i+1}"] = TestStatus.SKIPPED.value
        else:
            # 如果无法解析总数，至少标记一个测试为SKIPPED
            test_status_map["test_skipped_network_error"] = TestStatus.SKIPPED.value
        return test_status_map
    '''

    for line in log.split("\n"):
        line = line.strip()
        if any([line.startswith(x.value) for x in TestStatus]):
            if line.startswith(TestStatus.FAILED.value):
                line = line.replace(" - ", " ")
            test_case = line.split()
            if len(test_case) >= 2:
                test_status_map[test_case[1]] = test_case[0]
    return test_status_map


def parse_log_pytest_options(log: str) -> Dict[str, str]:
    """
    Parser for test logs generated with PyTest framework with options
    处理带选项的pytest输出，如requests、pylint等

    Args:
        log (str): log content
    Returns:
        dict: test case to test status mapping
    """
    option_pattern = re.compile(r"(.*?)\[(.*)\]")
    test_status_map = {}

    # 检查是否包含HTTP错误（包括502 Bad Gateway等）
    '''
    if "HTTP Error 502" in log or "Bad Gateway" in log or "urllib.error.HTTPError" in log:
        # 对于包含HTTP错误的日志，将相关测试标记为SKIPPED
        # 尝试解析测试总数
        test_count_match = re.search(r'(?:collected|found)\s+(\d+)\s+(?:tests?|items?)', log)
        if test_count_match:
            test_count = int(test_count_match.group(1))
            for i in range(test_count):
                test_status_map[f"test_skipped_{i+1}"] = TestStatus.SKIPPED.value
        else:
            # 如果无法解析总数，至少标记一个测试为SKIPPED
            test_status_map["test_skipped_network_error"] = TestStatus.SKIPPED.value
        return test_status_map
    '''

    for line in log.split("\n"):
        if any([line.startswith(x.value) for x in TestStatus]):
            # Additional parsing for FAILED status
            if line.startswith(TestStatus.FAILED.value):
                line = line.replace(" - ", " ")
            test_case = line.split()
            if len(test_case) <= 1:
                continue
            has_option = option_pattern.search(test_case[1])
            if has_option:
                main, option = has_option.groups()
                if (
                    option.startswith("/")
                    and not option.startswith("//")
                    and "*" not in option
                ):
                    option = "/" + option.split("/")[-1]
                test_name = f"{main}[{option}]"
            else:
                test_name = test_case[1]
            test_status_map[test_name] = test_case[0]
    return test_status_map


def parse_log_pytest_v2(log: str) -> Dict[str, str]:
    """Parser for PyTest framework logs (later version)"""
    test_status_map = {}

    # 检查是否包含HTTP错误（包括502 Bad Gateway等）
    '''
    if "HTTP Error 502" in log or "Bad Gateway" in log or "urllib.error.HTTPError" in log:
        # 对于包含HTTP错误的日志，将相关测试标记为SKIPPED
        # 尝试解析测试总数
        test_count_match = re.search(r'(?:collected|found)\s+(\d+)\s+(?:tests?|items?)', log)
        if test_count_match:
            test_count = int(test_count_match.group(1))
            for i in range(test_count):
                test_status_map[f"test_skipped_{i+1}"] = TestStatus.SKIPPED.value
        else:
            # 如果无法解析总数，至少标记一个测试为SKIPPED
            test_status_map["test_skipped_network_error"] = TestStatus.SKIPPED.value
        return test_status_map
    '''

    # Clean ANSI escape codes
    escapes = "".join([chr(char) for char in range(1, 32)])
    for line in log.split("\n"):
        line = re.sub(r"\[(\d+)m", "", line)
        translator = str.maketrans("", "", escapes)
        line = line.translate(translator)

        # Handle standard pytest status lines like "PASSED test_name" or "test_name PASSED"
        if any([line.startswith(x.value) for x in TestStatus]):
            # Special handling for SKIPPED lines with count format: "SKIPPED [8] filename:line: reason"
            if line.startswith(TestStatus.SKIPPED.value) and "[" in line and "]" in line:
                # Extract count from [count] format
                count_match = re.search(r'\[(\d+)\]', line)
                if count_match:
                    count = int(count_match.group(1))
                    # Extract test name from the line (after the count part)
                    parts = line.split("]", 1)
                    if len(parts) > 1:
                        test_info = parts[1].strip()
                        # Use a descriptive name for the skipped test group
                        test_status_map[f"skipped_group_{len(test_status_map)}"] = TestStatus.SKIPPED.value
                    else:
                        # Fallback: create individual skipped test entries
                        for i in range(count):
                            test_status_map[f"skipped_test_{len(test_status_map)}"] = TestStatus.SKIPPED.value
                continue

            if line.startswith(TestStatus.FAILED.value):
                line = line.replace(" - ", " ")
            test_case = line.split()
            if len(test_case) >= 2:
                test_status_map[test_case[1]] = test_case[0]
        # Support older pytest versions by checking if the line ends with the test status
        elif any([line.endswith(x.value) for x in TestStatus]):
            test_case = line.split()
            if len(test_case) >= 2:
                test_status_map[test_case[0]] = test_case[1]

        # Handle pytest summary line: "366 passed, 1 warnings in 2.99 seconds"
        elif "passed" in line.lower() and any(word in line.lower() for word in ["warnings", "seconds"]):
            # Extract counts from summary line
            passed_match = re.search(r'(\d+)\s*passed', line, re.IGNORECASE)
            failed_match = re.search(r'(\d+)\s*failed', line, re.IGNORECASE)
            skipped_match = re.search(r'(\d+)\s*skipped', line, re.IGNORECASE)
            error_match = re.search(r'(\d+)\s*error', line, re.IGNORECASE)

            if passed_match:
                # Create synthetic test entries for summary counts
                passed_count = int(passed_match.group(1))
                for i in range(passed_count):
                    test_status_map[f"test_passed_{i+1}"] = TestStatus.PASSED.value

            if failed_match:
                failed_count = int(failed_match.group(1))
                for i in range(failed_count):
                    test_status_map[f"test_failed_{i+1}"] = TestStatus.FAILED.value

            if skipped_match:
                skipped_count = int(skipped_match.group(1))
                for i in range(skipped_count):
                    test_status_map[f"test_skipped_{i+1}"] = TestStatus.SKIPPED.value

            if error_match:
                error_count = int(error_match.group(1))
                for i in range(error_count):
                    test_status_map[f"test_error_{i+1}"] = TestStatus.ERROR.value

    # If we don't have individual test results, try to parse the test result line
    # like: "astropy/utils/tests/test_misc.py ..s......                               [100%]"
    if not any(not key.startswith("test_") for key in test_status_map.keys()):
        for line in log.split("\n"):
            line = line.strip()
            # Look for lines that contain test file and status dots
            # Pattern: "filepath ...dots... [percentage%]"
            if "..." in line and ("[" in line or "%" in line) and any(c in line for c in [".", "s", "F", "E"]):
                # Extract the dots part which shows individual test results
                parts = line.split(" ... ")
                if len(parts) >= 2:
                    status_part = parts[1].split()[0]  # Get the dots before percentage
                    # Remove any trailing characters that aren't status indicators
                    status_part = re.sub(r'[^\.sFE]', '', status_part)

                    # Parse individual test results from dots
                    test_index = 1
                    for char in status_part:
                        if char == '.':
                            test_status_map[f"test_passed_{test_index}"] = TestStatus.PASSED.value
                        elif char == 's':
                            test_status_map[f"test_skipped_{test_index}"] = TestStatus.SKIPPED.value
                        elif char == 'F':
                            test_status_map[f"test_failed_{test_index}"] = TestStatus.FAILED.value
                        elif char == 'E':
                            test_status_map[f"test_error_{test_index}"] = TestStatus.ERROR.value
                        test_index += 1

    return test_status_map


def parse_log_django(log: str) -> Dict[str, str]:
    """Parser for Django test framework logs"""
    test_status_map = {}

    # 检查是否包含HTTP 502错误，如果是则跳过这个测试
    '''
    if "HTTP Error 502: Bad Gateway" in log or "urllib.error.HTTPError" in log:
        # 对于包含502错误的日志，将所有测试标记为SKIPPED
        # 解析测试总数
        total_match = re.search(r"Ran (\d+) tests", log)
        if total_match:
            total_tests = int(total_match.group(1))
            for i in range(total_tests):
                test_status_map[f"test_skipped_{i+1}"] = TestStatus.SKIPPED.value
        return test_status_map
    '''

    # Parse individual test results - handle both formats:
    # 1. "test_name (module.TestClass) ... ok"
    # 2. "test_name ... ok"
    for line in log.split("\n"):
        line = line.strip()
        if " ... " in line:
            parts = line.split(" ... ")
            if len(parts) == 2:
                test_name_part = parts[0].strip()
                status_part = parts[1].strip()

                # Extract test name from format like "test_name (module.TestClass)"
                if " (" in test_name_part and test_name_part.endswith(")"):
                    test_name = test_name_part.split(" (")[0]
                else:
                    test_name = test_name_part

                if status_part.lower() == "ok":
                    test_status_map[test_name] = TestStatus.PASSED.value
                elif status_part.lower() == "fail":
                    test_status_map[test_name] = TestStatus.FAILED.value
                elif status_part.lower() == "error":
                    test_status_map[test_name] = TestStatus.ERROR.value
                elif "skipped" in status_part.lower():
                    test_status_map[test_name] = TestStatus.SKIPPED.value

    # Parse FAIL: and ERROR: lines
    for line in log.split("\n"):
        line = line.strip()
        if line.startswith("FAIL:"):
            test_name = line.split()[1].strip()
            test_status_map[test_name] = TestStatus.FAILED.value
        if line.startswith("ERROR:"):
            test_name = line.split()[1].strip()
            test_status_map[test_name] = TestStatus.ERROR.value

    # If we have individual test results, return them
    if test_status_map:
        return test_status_map

    # If no individual test results found, try to parse summary
    # Look for summary line like "FAILED (errors=1, skipped=116)" or just "OK"
    summary_pattern = r"(?:FAILED|OK|PASSED|ERROR).*?(?:\((.*?)\))?"
    summary_match = re.search(summary_pattern, log, re.IGNORECASE)

    if summary_match:
        summary_text = summary_match.group(0).strip()
        summary_part = summary_match.group(1) if summary_match.group(1) else ""

        # Check if it's just "OK" (all tests passed)
        if summary_text.upper() == "OK":
            total_match = re.search(r"Ran (\d+) tests", log)
            if total_match:
                total_tests = int(total_match.group(1))
                for i in range(total_tests):
                    test_status_map[f"test_passed_{i+1}"] = TestStatus.PASSED.value
                return test_status_map

        # Parse detailed summary like "FAILED (errors=1, skipped=116)"
        if summary_part:
            # Parse counts from summary
            counts = {}
            for part in summary_part.split(','):
                part = part.strip()
                if '=' in part:
                    key, value = part.split('=')
                    counts[key.strip()] = int(value.strip())

            # Create dummy test entries based on counts
            test_index = 1
            for status_key, count in counts.items():
                status = TestStatus.FAILED.value  # default
                if 'error' in status_key.lower():
                    status = TestStatus.ERROR.value
                elif 'skipped' in status_key.lower():
                    status = TestStatus.SKIPPED.value
                elif 'passed' in status_key.lower():
                    status = TestStatus.PASSED.value

                for i in range(count):
                    test_status_map[f"test_{status_key}_{test_index}"] = status
                    test_index += 1

            # Add remaining tests as passed (total - failed - errors - skipped)
            total_match = re.search(r"Ran (\d+) tests", log)
            if total_match:
                total_tests = int(total_match.group(1))
                passed_tests = total_tests - sum(counts.values())
                for i in range(passed_tests):
                    test_status_map[f"test_passed_{i+1}"] = TestStatus.PASSED.value

    return test_status_map


def parse_log_sympy(log: str) -> Dict[str, str]:
    """Parser for SymPy test framework logs"""
    test_status_map = {}
    pattern = r"(_*) (.*)\.py:(.*) (_*)"
    matches = re.findall(pattern, log)
    for match in matches:
        test_case = f"{match[1]}.py:{match[2]}"
        test_status_map[test_case] = TestStatus.FAILED.value

    for line in log.split("\n"):
        line = line.strip()
        if line.startswith("test_"):
            if line.endswith(" E"):
                test = line.split()[0]
                test_status_map[test] = TestStatus.ERROR.value
            if line.endswith(" F"):
                test = line.split()[0]
                test_status_map[test] = TestStatus.FAILED.value
            if line.endswith(" ok"):
                test = line.split()[0]
                test_status_map[test] = TestStatus.PASSED.value
    return test_status_map


# SWE-bench兼容的解析器别名
parse_log_astroid = parse_log_pytest
parse_log_flask = parse_log_pytest
parse_log_marshmallow = parse_log_pytest
parse_log_pvlib = parse_log_pytest
parse_log_pyvista = parse_log_pytest
parse_log_sqlfluff = parse_log_pytest
parse_log_xarray = parse_log_pytest

parse_log_pydicom = parse_log_pytest_options
parse_log_requests = parse_log_pytest_options
parse_log_pylint = parse_log_pytest_options

parse_log_astropy = parse_log_pytest_v2
parse_log_scikit = parse_log_pytest_v2
parse_log_sphinx = parse_log_pytest_v2

parse_log_matplotlib = parse_log_pytest
parse_log_seaborn = parse_log_pytest

MAP_REPO_TO_PARSER_PY = {
    "astropy/astropy": parse_log_astropy,
    "django/django": parse_log_django,
    "marshmallow-code/marshmallow": parse_log_marshmallow,
    "matplotlib/matplotlib": parse_log_matplotlib,
    "mwaskom/seaborn": parse_log_seaborn,
    "pallets/flask": parse_log_flask,
    "psf/requests": parse_log_requests,
    "pvlib/pvlib-python": parse_log_pvlib,
    "pydata/xarray": parse_log_xarray,
    "pydicom/pydicom": parse_log_pydicom,
    "pylint-dev/astroid": parse_log_astroid,
    "pylint-dev/pylint": parse_log_pylint,
    "pytest-dev/pytest": parse_log_pytest,
    "pyvista/pyvista": parse_log_pyvista,
    "scikit-learn/scikit-learn": parse_log_scikit,
    "sqlfluff/sqlfluff": parse_log_sqlfluff,
    "sphinx-doc/sphinx": parse_log_sphinx,
    "sympy/sympy": parse_log_sympy,
}


def get_repo_from_instance_id(instance_id: str) -> str:
    """Extract repository name from instance_id - 支持所有SWE-bench仓库"""

    # SWE-bench仓库映射表
    repo_mappings = {
        # 格式: 前缀 -> 完整仓库名
        "astropy": "astropy/astropy",
        "django": "django/django",
        "matplotlib": "matplotlib/matplotlib",
        "mwaskom": "mwaskom/seaborn",
        "psf": "psf/requests",
        "pydata": "pydata/xarray",
        "pylint-dev": "pylint-dev/pylint",
        "scikit-learn": "scikit-learn/scikit-learn",
        "sphinx-doc": "sphinx-doc/sphinx",
        "sympy": "sympy/sympy",
        # 支持其他可能的仓库
        "marshmallow-code": "marshmallow-code/marshmallow",
        "pallets": "pallets/flask",
        "pvlib": "pvlib/pvlib-python",
        "pydicom": "pydicom/pydicom",
        "pytest-dev": "pytest-dev/pytest",
        "pyvista": "pyvista/pyvista",
        "sqlfluff": "sqlfluff/sqlfluff"
    }

    # Handle formats like "astropy-7166" or "django-10097"
    if "-" in instance_id:
        repo_part = instance_id.split("-")[0]
        if repo_part in repo_mappings:
            return repo_mappings[repo_part]

    # Handle formats with "__" separator (SWE-bench标准格式)
    if "__" in instance_id:
        repo_part = instance_id.split("__")[0]
        if repo_part in repo_mappings:
            return repo_mappings[repo_part]

    # 特殊处理一些复合名称
    if "matplotlib" in instance_id:
        return "matplotlib/matplotlib"
    elif "seaborn" in instance_id:
        return "mwaskom/seaborn"
    elif "requests" in instance_id:
        return "psf/requests"
    elif "xarray" in instance_id:
        return "pydata/xarray"
    elif "pylint" in instance_id:
        return "pylint-dev/pylint"
    elif "scikit-learn" in instance_id:
        return "scikit-learn/scikit-learn"
    elif "sphinx" in instance_id:
        return "sphinx-doc/sphinx"

    return "unknown/unknown"


# MARK: SWE-bench Standard Evaluation Functions

# def test_passed_swebench(case: str, status_map: Dict[str, str]) -> bool:
#     """
#     SWE-bench标准：测试是否通过
#     测试在状态映射中且状态是PASSED或XFAIL
#     """
#     return case in status_map and status_map[case] in [TestStatus.PASSED.value, TestStatus.XFAIL.value]


# def test_failed_swebench(case: str, status_map: Dict[str, str]) -> bool:
#     """
#     SWE-bench标准：测试是否失败
#     测试不在状态映射中或者状态是FAILED或ERROR
#     """
#     return case not in status_map or status_map[case] in [TestStatus.FAILED.value, TestStatus.ERROR.value]


# def compute_fail_to_pass_swebench(f2p_success: int, f2p_failure: int) -> float:
#     """
#     计算F2P (Fail-to-Pass) 成功率 - SWE-bench标准
#     """
#     total = f2p_success + f2p_failure
#     if total == 0:
#         return 1.0  # 如果没有F2P测试，认为成功
#     return f2p_success / total


# def compute_pass_to_pass_swebench(p2p_success: int, p2p_failure: int) -> float:
#     """
#     计算P2P (Pass-to-Pass) 成功率 - SWE-bench标准
#     """
#     total = p2p_success + p2p_failure
#     if total == 0:
#         return 1.0  # 如果没有P2P测试，认为成功
#     return p2p_success / total


# def get_resolution_status_swebench(f2p_rate: float, p2p_rate: float) -> str:
#     """
#     根据F2P和P2P成功率判断最终状态 - SWE-bench标准
#     """
#     if f2p_rate == 1.0 and p2p_rate == 1.0:
#         return ResolvedStatus.FULL.value
#     elif f2p_rate < 1.0 and f2p_rate > 0.0 and p2p_rate == 1.0:
#         return ResolvedStatus.PARTIAL.value
#     else:
#         return ResolvedStatus.NO.value


# def get_eval_tests_report_swebench(
#     eval_status_map: Dict[str, str],
#     gold_results: Dict[str, List[str]]
# ) -> Dict[str, Dict[str, List[str]]]:
#     """
#     创建基于gold results的评估报告 - SWE-bench标准

#     Args:
#         eval_status_map: 评估结果状态映射
#         gold_results: gold标准结果，包含FAIL_TO_PASS和PASS_TO_PASS

#     Returns:
#         评估报告
#     """
#     f2p_success = []
#     f2p_failure = []

#     # 计算F2P指标
#     if "FAIL_TO_PASS" in gold_results:
#         for test_case in gold_results["FAIL_TO_PASS"]:
#             if test_passed_swebench(test_case, eval_status_map):
#                 f2p_success.append(test_case)
#             elif test_failed_swebench(test_case, eval_status_map):
#                 f2p_failure.append(test_case)

#     # 计算P2P指标
#     p2p_success = []
#     p2p_failure = []

#     if "PASS_TO_PASS" in gold_results:
#         for test_case in gold_results["PASS_TO_PASS"]:
#             if test_passed_swebench(test_case, eval_status_map):
#                 p2p_success.append(test_case)
#             elif test_failed_swebench(test_case, eval_status_map):
#                 p2p_failure.append(test_case)

#     return {
#         "FAIL_TO_PASS": {
#             "success": f2p_success,
#             "failure": f2p_failure,
#         },
#         "PASS_TO_PASS": {
#             "success": p2p_success,
#             "failure": p2p_failure,
#         },
#     }


def analyze_test_results(test_status_map: Dict[str, str]) -> Tuple[int, int, int, int]:
    """
    Analyze test results and return counts:
    (passed, failed, skipped, error)

    Note: XFAIL is considered PASSED in SWE-bench standard
    """
    passed = 0
    failed = 0
    skipped = 0
    error = 0

    for status in test_status_map.values():
        if status == TestStatus.PASSED.value:
            passed += 1
        elif status == TestStatus.XFAIL.value:
            # XFAIL is considered PASSED in SWE-bench standard
            passed += 1
        elif status == TestStatus.FAILED.value:
            failed += 1
        elif status == TestStatus.SKIPPED.value:
            skipped += 1
        elif status == TestStatus.ERROR.value:
            error += 1

    return passed, failed, skipped, error


def load_gold_results(gold_results_path: str) -> Dict[str, Dict[str, List[str]]]:
    """
    加载gold results文件 (支持JSON和JSONL格式)

    Args:
        gold_results_path: gold results文件路径 (JSON或JSONL格式)

    Returns:
        gold results字典，格式: {instance_id: {"FAIL_TO_PASS": [...], "PASS_TO_PASS": [...]}}
    """
    try:
        if gold_results_path.endswith('.jsonl'):
            # JSONL格式：每行一个JSON对象
            gold_data = {}
            with open(gold_results_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        instance_data = json.loads(line)
                        instance_id = instance_data.get('instance_id', '')
                        if instance_id:
                            # 处理FAIL_TO_PASS和PASS_TO_PASS，如果是字符串则解析为列表
                            fail_to_pass = instance_data.get("FAIL_TO_PASS", [])
                            pass_to_pass = instance_data.get("PASS_TO_PASS", [])

                            # 如果是字符串，尝试解析为JSON数组
                            if isinstance(fail_to_pass, str):
                                try:
                                    fail_to_pass = json.loads(fail_to_pass)
                                except json.JSONDecodeError:
                                    fail_to_pass = []
                            if isinstance(pass_to_pass, str):
                                try:
                                    pass_to_pass = json.loads(pass_to_pass)
                                except json.JSONDecodeError:
                                    pass_to_pass = []

                            gold_data[instance_id] = {
                                "FAIL_TO_PASS": fail_to_pass,
                                "PASS_TO_PASS": pass_to_pass
                            }
                    except json.JSONDecodeError as e:
                        print(f"警告：第{line_num}行JSON解析错误: {e}")
                        continue
            return gold_data
        else:
            # 传统JSON格式
            with open(gold_results_path, 'r', encoding='utf-8') as f:
                gold_data = json.load(f)

            # 支持不同的gold results格式
            if isinstance(gold_data, dict):
                # 如果是字典格式
                return {
                    "global": {
                        "FAIL_TO_PASS": gold_data.get("FAIL_TO_PASS", []),
                        "PASS_TO_PASS": gold_data.get("PASS_TO_PASS", [])
                    }
                }
            elif isinstance(gold_data, list):
                # 如果是列表格式（假设是FAIL_TO_PASS列表）
                return {
                    "global": {
                        "FAIL_TO_PASS": gold_data,
                        "PASS_TO_PASS": []
                    }
                }
            else:
                print(f"警告：未知的gold results格式: {type(gold_data)}")
                return {}

    except FileNotFoundError:
        print(f"错误：找不到gold results文件 {gold_results_path}")
        return {}
    except json.JSONDecodeError:
        print(f"错误：gold results JSON格式错误 {gold_results_path}")
        return {}


# def evaluate_instance_swebench(
#     instance_id: str,
#     eval_status_map: Dict[str, str],
#     gold_results: Dict[str, List[str]]
# ) -> Dict[str, Any]:
#     """
#     使用SWE-bench标准评估单个实例

#     Args:
#         instance_id: 实例ID
#         eval_status_map: 评估结果状态映射
#         gold_results: gold标准结果

#     Returns:
#         SWE-bench评估结果
#     """
#     # 生成评估报告
#     report = get_eval_tests_report_swebench(eval_status_map, gold_results)

#     # 计算F2P和P2P成功率
#     f2p_success_count = len(report["FAIL_TO_PASS"]["success"])
#     f2p_failure_count = len(report["FAIL_TO_PASS"]["failure"])
#     p2p_success_count = len(report["PASS_TO_PASS"]["success"])
#     p2p_failure_count = len(report["PASS_TO_PASS"]["failure"])

#     f2p_rate = compute_fail_to_pass_swebench(f2p_success_count, f2p_failure_count)
#     p2p_rate = compute_pass_to_pass_swebench(p2p_success_count, p2p_failure_count)

#     # 获取最终状态
#     resolution_status = get_resolution_status_swebench(f2p_rate, p2p_rate)

#     return {
#         "instance_id": instance_id,
#         "f2p_success": f2p_success_count,
#         "f2p_failure": f2p_failure_count,
#         "p2p_success": p2p_success_count,
#         "p2p_failure": p2p_failure_count,
#         "f2p_rate": f2p_rate,
#         "p2p_rate": p2p_rate,
#         "resolution_status": resolution_status,
#         "report": report
#     }


def process_log_file(log_path: str, gold_results: Dict[str, List[str]] = None) -> Tuple[str, str, Dict[str, str]]:
    """
    Process a single log file and return:
    (instance_id, test_type, test_status_map)

    Args:
        log_path: Path to log file
        gold_results: Dictionary mapping instance_id to gold test results
                     Format: {"FAIL_TO_PASS": [...], "PASS_TO_PASS": [...]}
    """
    # Extract instance_id and test_type from filename
    filename = Path(log_path).name
    if "__" not in filename:
        return None, None, {}

    parts = filename.replace(".log", "").split("__")
    if len(parts) != 2:
        return None, None, {}

    repo_part = parts[0]  # e.g., "astropy"
    instance_id_part = parts[1]  # e.g., "astropy-13033_p2p"
    test_type = instance_id_part.split("_")[-1]  # f2p or p2p
    instance_short_id = instance_id_part.replace(f"_{test_type}", "")  # e.g., "astropy-13033"

    # Construct full instance_id to match gold results format
    instance_id = f"{repo_part}__{instance_short_id}"

    # Read log content
    with open(log_path, 'r', encoding='utf-8') as f:
        log_content = f.read()

    # Extract test output
    test_output, is_timeout = extract_test_output_from_log(log_content)

    # Handle timeout case
    if is_timeout:
        # For timeout, create a special timeout status map
        test_status_map = {"timeout_instance": TestStatus.TIMEOUT.value}
        return instance_id, test_type, test_status_map

    # Get appropriate parser
    repo = get_repo_from_instance_id(instance_id)
    parser = MAP_REPO_TO_PARSER_PY.get(repo, parse_log_pytest)

    # Parse test results
    test_status_map = parser(test_output)

    # Filter tests based on gold results if provided
    if gold_results:
        expected_tests = []
        if test_type.upper() == "F2P" and "FAIL_TO_PASS" in gold_results:
            expected_tests = gold_results["FAIL_TO_PASS"]
        elif test_type.upper() == "P2P" and "PASS_TO_PASS" in gold_results:
            expected_tests = gold_results["PASS_TO_PASS"]

        # Filter test_status_map to only include expected tests
        filtered_status_map = {}

        # If we have synthetic test names like test_passed_1, test_passed_2, etc.
        # and we have expected tests, map them by order
        if expected_tests and test_status_map:
            synthetic_keys = sorted([k for k in test_status_map.keys() if k.startswith("test_")])
            if synthetic_keys:
                # Map synthetic test names to expected test names by index
                for i, synthetic_key in enumerate(synthetic_keys):
                    if i < len(expected_tests):
                        filtered_status_map[expected_tests[i]] = test_status_map[synthetic_key]
            else:
                # If we have real test names, filter normally
                for test_name, status in test_status_map.items():
                    if test_name in expected_tests:
                        filtered_status_map[test_name] = status
        else:
            # No filtering needed or no expected tests
            filtered_status_map = test_status_map

        test_status_map = filtered_status_map

    return instance_id, test_type, test_status_map


def main():
    """Main function to process all logs and generate results"""
    import argparse

    parser = argparse.ArgumentParser(description="SWE-bench Log Parser")
    parser.add_argument("--logs-dir", default="/mnt/cfs_bj_mt/workspace/xuruijie/rollout/0901-test-log-v10/validations",
                       help="Directory containing log files")
    parser.add_argument("--gold-results", default="/mnt/cfs_bj_mt/workspace/xuruijie/swebench/agentic-rollout-library/test-00000-of-00001-with-images.jsonl", help="Path to gold results JSON file for SWE-bench evaluation")
    parser.add_argument("--output", default="/mnt/cfs_bj_mt/workspace/xuruijie/rollout/0901-test-log-v10/validations/test_results_analysis.json",
                       help="Output JSON file path")
    parser.add_argument("--exclude-file", type=str, default=None,
                       help="Path to JSON file containing instance IDs to exclude from analysis")

    args = parser.parse_args()

    logs_dir = args.logs_dir
    gold_results_path = args.gold_results
    output_file = args.output
    exclude_file_path = args.exclude_file

    # Load gold results if provided
    gold_results = None
    #gold_results_path = None
    if gold_results_path:
        gold_results = load_gold_results(gold_results_path)
        print(f"Loaded gold results from {gold_results_path}")
        if isinstance(gold_results, dict) and 'global' in gold_results:
            # 传统格式
            print(f"F2P tests: {len(gold_results['global']['FAIL_TO_PASS'])}")
            print(f"P2P tests: {len(gold_results['global']['PASS_TO_PASS'])}")
        else:
            # JSONL格式
            total_f2p = sum(len(data.get('FAIL_TO_PASS', [])) for data in gold_results.values())
            total_p2p = sum(len(data.get('PASS_TO_PASS', [])) for data in gold_results.values())
            print(f"Total instances: {len(gold_results)}")
            print(f"Total F2P tests: {total_f2p}")
            print(f"Total P2P tests: {total_p2p}")

    # Load exclude instance IDs from separate file if provided
    exclude_instance_ids = set()
    if exclude_file_path:
        try:
            # Check if exclude file is the same as gold results file
            if exclude_file_path == gold_results_path and gold_results:
                # Use already loaded gold results data
                if isinstance(gold_results, dict) and 'global' not in gold_results:
                    # JSONL format: extract instance_ids from gold_results
                    exclude_instance_ids = set(gold_results.keys())
                else:
                    print(f"警告：gold results文件格式不支持直接用作排除文件")
            else:
                # Load exclude file separately
                if exclude_file_path.endswith('.jsonl'):
                    # JSONL format (like gold results)
                    exclude_data = {}
                    with open(exclude_file_path, 'r', encoding='utf-8') as f:
                        for line_num, line in enumerate(f, 1):
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                instance_data = json.loads(line)
                                instance_id = instance_data.get('instance_id', '')
                                if instance_id:
                                    exclude_data[instance_id] = instance_data
                            except json.JSONDecodeError as e:
                                print(f"警告：第{line_num}行JSON解析错误: {e}")
                                continue
                    exclude_instance_ids = set(exclude_data.keys())
                else:
                    # Regular JSON format
                    with open(exclude_file_path, 'r', encoding='utf-8') as f:
                        exclude_data = json.load(f)

                    if isinstance(exclude_data, list):
                        # JSON array format: ["instance_id1", "instance_id2", ...]
                        exclude_instance_ids = set(exclude_data)
                    elif isinstance(exclude_data, dict):
                        # JSON object format: {"exclude": ["instance_id1", "instance_id2", ...]}
                        if "exclude" in exclude_data:
                            exclude_instance_ids = set(exclude_data["exclude"])
                        elif "instance_ids" in exclude_data:
                            exclude_instance_ids = set(exclude_data["instance_ids"])
                        else:
                            # Assume all keys are instance IDs
                            exclude_instance_ids = set(exclude_data.keys())
                    else:
                        print(f"警告：排除文件 {exclude_file_path} 格式不正确，期望是JSON数组或对象")

            print(f"Loaded {len(exclude_instance_ids)} instance IDs to exclude from {exclude_file_path}")

        except FileNotFoundError:
            print(f"错误：找不到排除文件 {exclude_file_path}")
        except json.JSONDecodeError as e:
            print(f"错误：排除文件JSON格式错误 {exclude_file_path}: {e}")

    # Group results by instance_id
    results = {}

    # Process all log files
    log_files = list(Path(logs_dir).glob("*.log"))
    print(f"Found {len(log_files)} log files")

    for log_file in log_files:
        # Get gold results for this instance if available
        instance_gold_results = None
        if gold_results:
            # Try to extract instance_id from filename first
            filename = Path(log_file).name
            if "__" in filename:
                parts = filename.replace(".log", "").split("__")
                if len(parts) >= 2:
                    repo_part = parts[-2]  # 倒数第二个部分是仓库名
                    instance_test_part = parts[-1]  # 最后一个部分是实例ID+测试类型

                    # 解析实例ID和测试类型
                    if "_" in instance_test_part:
                        test_type = instance_test_part.split("_")[-1]
                        instance_id_short = instance_test_part.replace(f"_{test_type}", "")
                        instance_id_temp = f"{repo_part}__{instance_id_short}"

                        if instance_id_temp in gold_results:
                            instance_gold_results = gold_results[instance_id_temp]
                        elif 'global' in gold_results:
                            instance_gold_results = gold_results['global']

        instance_id, test_type, test_status_map = process_log_file(str(log_file), instance_gold_results)

        if instance_id is None:
            continue

        if instance_id not in results:
            results[instance_id] = {"f2p": {}, "p2p": {}}

        results[instance_id][test_type] = test_status_map

    print(f"Processed {len(results)} instances")

    # Analyze results
    analysis = {
        "all_passed": [],  # Both f2p and p2p passed
        "f2p_only": [],    # Only f2p passed
        "p2p_only": [],    # Only p2p passed
        "none_passed": [], # Neither passed
        "timeout": [],     # Timeout instances
        "details": {}
    }

    # SWE-bench评估结果（已移除）
    # if gold_results:
    #     analysis["swebench_evaluation"] = {
    #         "full_resolved": [],
    #         "partial_resolved": [],
    #         "no_resolved": []
    #     }

    for instance_id, test_results in results.items():
        # Skip instance IDs that are in the exclude list
        if instance_id in exclude_instance_ids:
            continue

        f2p_results = test_results.get("f2p", {})
        p2p_results = test_results.get("p2p", {})

        # Check for timeout instances
        is_timeout = False
        if f2p_results and any(status == TestStatus.TIMEOUT.value for status in f2p_results.values()):
            is_timeout = True
        if p2p_results and any(status == TestStatus.TIMEOUT.value for status in p2p_results.values()):
            is_timeout = True

        if is_timeout:
            analysis["timeout"].append(instance_id)
            # Store timeout details
            instance_detail = {
                "f2p": {"pass_rate": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0, "total": 0, "timeout": True},
                "p2p": {"pass_rate": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0, "total": 0, "timeout": True}
            }
            analysis["details"][instance_id] = instance_detail
            continue  # Skip further analysis for timeout instances

        # Analyze f2p results
        if f2p_results:
            f2p_passed, f2p_failed, f2p_skipped, f2p_error = analyze_test_results(f2p_results)
            f2p_total = f2p_passed + f2p_failed + f2p_skipped + f2p_error
            f2p_pass_rate = f2p_passed / f2p_total if f2p_total > 0 else 0
            # 计算实际执行的测试通过率（不包括跳过的测试）
            f2p_total_executed = f2p_passed + f2p_failed + f2p_error
            f2p_pass_rate_executed = f2p_passed / f2p_total_executed if f2p_total_executed > 0 else 0
            # 只有当没有失败和错误时，才算完全通过（100%）
            f2p_passed_bool = f2p_failed == 0 and f2p_error == 0
        else:
            f2p_pass_rate = 0
            f2p_pass_rate_executed = 0
            f2p_passed_bool = False
            f2p_passed = f2p_failed = f2p_skipped = f2p_error = f2p_total = 0

        # Analyze p2p results
        if p2p_results:
            p2p_passed, p2p_failed, p2p_skipped, p2p_error = analyze_test_results(p2p_results)
            p2p_total = p2p_passed + p2p_failed + p2p_skipped + p2p_error
            p2p_pass_rate = p2p_passed / p2p_total if p2p_total > 0 else 0
            # 计算实际执行的测试通过率（不包括跳过的测试）
            p2p_total_executed = p2p_passed + p2p_failed + p2p_error
            p2p_pass_rate_executed = p2p_passed / p2p_total_executed if p2p_total_executed > 0 else 0
            # 所有测试都要求100%通过（完全无失败和错误）
            p2p_passed_bool = p2p_failed == 0 and p2p_error == 0
        else:
            p2p_pass_rate = 0
            p2p_pass_rate_executed = 0
            p2p_passed_bool = False
            p2p_passed = p2p_failed = p2p_skipped = p2p_error = p2p_total = 0

        # 修改评估标准：支持单侧测试通过，如果都没有测试则忽略
        # 如果P2P=100%且没有F2P测试，算通过；反之亦然
        f2p_good = bool(f2p_results) and f2p_pass_rate_executed > 0.99999  # 使用执行的测试通过率
        p2p_good = bool(p2p_results) and p2p_pass_rate_executed > 0.99999  # 使用执行的测试通过率

        # 情况1：如果F2P和P2P都没有测试，忽略（不计入任何统计）
        if not f2p_results and not p2p_results:
            # 忽略这个实例，不添加到任何分类中
            pass
        # 情况2：P2P=100%且没有F2P测试，算通过
        elif p2p_good and not f2p_results:
            analysis["all_passed"].append(instance_id)
        # 情况3：F2P=100%且没有P2P测试，算通过
        elif f2p_good and not p2p_results:
            analysis["all_passed"].append(instance_id)
        # 情况4：既有F2P又有P2P，都必须100%通过
        elif f2p_good and p2p_good:
            analysis["all_passed"].append(instance_id)
        else:
            # 其他情况算不通过
            analysis["none_passed"].append(instance_id)

        # Store detailed results
        instance_detail = {
            "f2p": {
                "pass_rate": f2p_pass_rate,
                "passed": f2p_passed,
                "failed": f2p_failed,
                "skipped": f2p_skipped,
                "error": f2p_error,
                "total": f2p_total
            },
            "p2p": {
                "pass_rate": p2p_pass_rate,
                "passed": p2p_passed,
                "failed": p2p_failed,
                "skipped": p2p_skipped,
                "error": p2p_error,
                "total": p2p_total
            }
        }

        # 添加SWE-bench评估结果（已移除）
        # if gold_results and instance_id in gold_results:
        #     swebench_eval = evaluate_instance_swebench(instance_id, f2p_results, gold_results[instance_id])
        #     instance_detail["swebench"] = {
        #         "f2p_rate": swebench_eval["f2p_rate"],
        #         "p2p_rate": swebench_eval["p2p_rate"],
        #         "resolution_status": swebench_eval["resolution_status"],
        #         "f2p_success": swebench_eval["f2p_success"],
        #         "f2p_failure": swebench_eval["f2p_failure"],
        #         "p2p_success": swebench_eval["p2p_success"],
        #         "p2p_failure": swebench_eval["p2p_failure"]
        #     }

        #     # 添加到SWE-bench统计中
        #     status = swebench_eval["resolution_status"]
        #     if status == ResolvedStatus.FULL.value:
        #         analysis["swebench_evaluation"]["full_resolved"].append(instance_id)
        #     elif status == ResolvedStatus.PARTIAL.value:
        #         analysis["swebench_evaluation"]["partial_resolved"].append(instance_id)
        #     else:
        #         analysis["swebench_evaluation"]["no_resolved"].append(instance_id)

        analysis["details"][instance_id] = instance_detail

    # Calculate overall statistics
    # Count instances that were actually processed (not excluded)
    processed_instances = 0
    for instance_id in results.keys():
        if instance_id not in exclude_instance_ids:
            processed_instances += 1

    total_instances = processed_instances
    # Count total instances found in logs and excluded instances
    total_instances_in_logs = len(results)
    excluded_count = sum(1 for instance_id in results.keys() if instance_id in exclude_instance_ids)

    analysis["summary"] = {
        "total_instances_in_logs": total_instances_in_logs,
        "excluded_instances": excluded_count,
        "processed_instances": total_instances,
        "all_passed_count": len(analysis["all_passed"]),
        "f2p_only_count": len(analysis["f2p_only"]),
        "p2p_only_count": len(analysis["p2p_only"]),
        "none_passed_count": len(analysis["none_passed"]),
        "timeout_count": len(analysis["timeout"]),
        "all_passed_rate": len(analysis["all_passed"]) / total_instances if total_instances > 0 else 0,
        "f2p_only_rate": len(analysis["f2p_only"]) / total_instances if total_instances > 0 else 0,
        "p2p_only_rate": len(analysis["p2p_only"]) / total_instances if total_instances > 0 else 0,
        "none_passed_rate": len(analysis["none_passed"]) / total_instances if total_instances > 0 else 0,
        "timeout_rate": len(analysis["timeout"]) / total_instances if total_instances > 0 else 0,
        # 添加新的统计信息
        "passed_rate": (len(analysis["all_passed"]) + len(analysis["f2p_only"]) + len(analysis["p2p_only"])) / total_instances if total_instances > 0 else 0,
    }
    print(analysis["summary"] )

    # 添加SWE-bench统计（已移除）
    # if gold_results:
    #     analysis["summary"]["swebench_full_rate"] = len(analysis["swebench_evaluation"]["full_resolved"]) / total_instances if total_instances > 0 else 0
    #     analysis["summary"]["swebench_partial_rate"] = len(analysis["swebench_evaluation"]["partial_resolved"]) / total_instances if total_instances > 0 else 0
    #     analysis["summary"]["swebench_no_rate"] = len(analysis["swebench_evaluation"]["no_resolved"]) / total_instances if total_instances > 0 else 0

    # Update final output message to include excluded count
    print(f"Analysis complete! Results saved to {output_file}")
    print(f"Found {total_instances_in_logs} instances in logs, excluded {excluded_count}, processed {total_instances}")


    # if gold_results:
    #     print(".2%")
    #     print(".2%")
    #     print(".2%")

    # Save results to JSON
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    print(f"Analysis complete! Results saved to {output_file}")
    print(f"Found {total_instances_in_logs} instances in logs, excluded {excluded_count}, processed {total_instances}")


    return analysis



if __name__ == "__main__":
    main()
