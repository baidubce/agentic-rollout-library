#!/usr/bin/env python3
"""
Argument utilities for Miaoda tools.

Provides base64 decoding support for command line arguments
that may contain special characters.
"""

import base64
from typing import Any


def decode_arg(value: Any) -> Any:
    """
    Decode an argument value if it's base64 encoded.

    Arguments with 'base64:' prefix are decoded from base64.
    Other values are returned as-is.

    Args:
        value: The argument value to decode

    Returns:
        Decoded value (string) or original value
    """
    if value is None:
        return None

    if not isinstance(value, str):
        return value

    if value.startswith('base64:'):
        try:
            encoded_part = value[7:]  # Remove 'base64:' prefix
            decoded_bytes = base64.b64decode(encoded_part)
            return decoded_bytes.decode('utf-8')
        except Exception as e:
            # If decoding fails, return original value
            print(f"Warning: Failed to decode base64 argument: {e}")
            return value

    return value


def decode_args(args) -> dict:
    """
    Decode all arguments in an argparse namespace.

    Args:
        args: argparse.Namespace object

    Returns:
        Dictionary with decoded argument values
    """
    result = {}
    for key, value in vars(args).items():
        result[key] = decode_arg(value)
    return result
