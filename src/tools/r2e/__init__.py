"""
R2E (Ready to Execute) Tools Package

Simple, standalone function-level tools that can be:
1. Used as Python functions
2. Executed as command-line tools
3. Integrated with agent systems
"""

from .bash_func import bash_func, parse_result, build_k8s_command

__all__ = [
    "bash_func",
    "parse_result",
    "build_k8s_command"
]