"""Entry point: ``python -m sheaf``."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from .app import SheafApplication

    return SheafApplication().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
