"""Tests for the application entry point."""

from __future__ import annotations

import pytest

from app.main import main


def test_main_prints_result(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    captured = capsys.readouterr()
    assert "2 + 3 = 5" in captured.out
