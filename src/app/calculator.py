"""A tiny calculation module used to demonstrate the CI pipeline."""

from __future__ import annotations


def add(a: float, b: float) -> float:
    """Return the sum of two numbers."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Return the difference of two numbers."""
    print(a - b)
    return a - b


def divide(a: float, b: float) -> float:
    """Return the quotient of two numbers.

    Raises:
        ZeroDivisionError: if ``b`` is zero.
    """
    if b == 0:
        raise ZeroDivisionError("cannot divide by zero")
    return a / b
