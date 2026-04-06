"""Typer CLI entrypoint.

Phase I placeholder. The CLI is the main operator interface.
Real commands land in a later phase. See docs/LOCKED_CONTEXT.md
for the locked command list.
"""

from . import __version__


def main() -> None:
    """Entrypoint stub for the `audit` CLI."""
    print(f"notion-audit-os v{__version__}")
    print("CLI not implemented yet — see docs/LOCKED_CONTEXT.md for planned commands.")


if __name__ == "__main__":
    main()
