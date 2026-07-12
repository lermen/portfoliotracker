# `__main__.py` is a special Python file. When you run a package with:
#   python -m portfolio
# Python executes this file. It is the standard entry point for runnable packages.
# It is also the target of the `portfolio` console script declared in pyproject.toml,
# so `portfolio ...` and `python -m portfolio ...` behave identically.
#
# This module is a thin *dispatcher*: it parses the command line and hands off to
# the right frontend. With no subcommand it launches the Textual TUI (the original
# behaviour); with `import` it runs the CLI importer. All real work lives in the
# `core`/`ui` layers — nothing below does portfolio logic itself.

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    """Define the command-line interface.

    `argparse` is Python's standard library for parsing command-line arguments.
    "Subparsers" let one program expose several sub-commands (like `git commit`
    and `git push`), each with its own arguments.
    """
    parser = argparse.ArgumentParser(
        prog="portfolio",
        description="Portfolio tracker — run the TUI, or manage positions from CLI.",
    )
    # `dest="command"` stores the chosen sub-command's name in `args.command`.
    # It stays `None` when the user runs `portfolio` with no sub-command.
    subparsers = parser.add_subparsers(dest="command")

    import_parser = subparsers.add_parser(
        "import",
        help="Update positions from a B3 'posição' export spreadsheet.",
        description=(
            "Read a B3 position export (.xlsx) and update the portfolio's tickers "
            "and quantities to match. Shows a plan and asks for confirmation before "
            "writing anything."
        ),
    )
    import_parser.add_argument(
        "file",
        help="Path to the B3 export .xlsx (e.g. posicao-2026-07-11-11-28-57.xlsx).",
    )
    import_parser.add_argument(
        "--portfolio",
        metavar="PATH",
        default=None,
        help="Portfolio file to update (default: the app's configured EXCEL_PATH).",
    )
    # `action="store_true"` makes a flag: present → True, absent → False.
    import_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Apply changes without the interactive confirmation prompt.",
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the plan but do not write any changes.",
    )
    import_parser.add_argument(
        "--add-new",
        action="store_true",
        help="Also add tickers found in the export but missing from the portfolio.",
    )

    web_parser = subparsers.add_parser(
        "web",
        help="Launch the NiceGUI-based web UI.",
        description=(
            "Serve the portfolio as a web app (dashboard, positions, allocation)."
        ),
    )
    web_parser.add_argument(
        "--host",
        default=None,
        help="Interface to bind (default: settings.web_host, i.e. 127.0.0.1).",
    )
    web_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to listen on (default: settings.web_port, i.e. 8080).",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "import":
        # Import the CLI frontend lazily — only when it's actually needed — so the
        # common case (launching the TUI) doesn't pay to import it.
        from portfolio.ui.cli.import_command import run

        exit_code = run(
            export_file=args.file,
            portfolio=args.portfolio,
            assume_yes=args.yes,
            dry_run=args.dry_run,
            add_new=args.add_new,
        )
        # `sys.exit(code)` ends the process with an exit status — 0 means success,
        # non-zero signals an error, which scripts and CI can check.
        sys.exit(exit_code)

    if args.command == "web":
        # Imported lazily so the default TUI path doesn't pay for NiceGUI/FastAPI
        # imports it never uses.
        from portfolio.core.settings import settings
        from portfolio.ui.web.app import run as run_web

        if args.host is not None:
            settings.web_host = args.host
        if args.port is not None:
            settings.web_port = args.port
        run_web()
        return

    # No sub-command: launch the Textual TUI (the default, original behaviour).
    # `.run()` starts the Textual event loop, which blocks until the user quits.
    from portfolio.ui.tui.app import PortfolioApp

    PortfolioApp().run()


# `if __name__ == "__main__"` is another Python convention:
# this block runs ONLY when the script is executed directly (not when imported).
# It prevents `main()` from being called accidentally when another module imports
# from this file.
if __name__ == "__main__":
    main()
