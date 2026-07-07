"""Application entry point."""

from __future__ import annotations

from app.calculator import add


def main() -> None:
    result = add(2, 3)
    print("++++++++++++++++++")
    print(f"2 + 3 = {result}")
    print("++++++++++++++++++")



if __name__ == "__main__":
    main()
