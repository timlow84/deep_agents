"""Unit tests for tools — no API key required."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.search import calculator


def test_calculator_basic():
    assert calculator.invoke("2 + 2") == "4"


def test_calculator_expression():
    assert calculator.invoke("42 * 17") == "714"


def test_calculator_invalid():
    result = calculator.invoke("not_a_number")
    assert "Error" in result
