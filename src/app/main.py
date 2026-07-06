"""Application entry point."""

from __future__ import annotations

from app.calculator import add


def main() -> None:
    result = add(2, 3)
    print(f"2 + 3 = {result}")


if __name__ == "__main__":
    main()
