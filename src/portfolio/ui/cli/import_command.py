# The `import` command: sync the portfolio from a B3 "posição" export.
#
# This is the *frontend* for the import feature. It owns everything the user sees
# and touches — printing the plan, asking for confirmation, exit codes — and
# delegates all the actual logic to `portfolio.core.importer`.
#
# Why `print()` here when the project bans it elsewhere?
# ------------------------------------------------------
# The "no print()" rule in CLAUDE.md is about *logging* — diagnostic output that
# belongs in structured logs. A CLI's whole job is to talk to the user on stdout,
# so print() is the right tool here, the same way the TUI draws widgets. The core
# layer still logs via structlog; only this frontend prints.

import asyncio
from pathlib import Path

from portfolio.core.exceptions import ConfigError, StorageError
from portfolio.core.importer import ImportPlan, apply_plan, build_plan
from portfolio.core.settings import settings
from portfolio.core.store import ExcelStore, PortfolioStore, get_store


def _fmt_qty(quantity: float) -> str:
    """Show whole numbers without a trailing '.0' (9105, not 9105.0)."""
    return f"{quantity:g}"


def _print_plan(
    plan: ImportPlan, export_path: Path, target: str, add_new: bool
) -> None:
    """Print a human-readable summary of what the import would change."""
    print(f"\nPortfolio import — {export_path.name}")
    print(f"Target: {target}\n")

    # --- Quantity updates ---
    print(f"Quantity updates ({len(plan.updates)}):")
    if plan.updates:
        # Align the arrows into a column by padding each ticker to the widest one.
        width = max(len(c.ticker) for c in plan.updates)
        for change in plan.updates:
            print(
                f"  {change.ticker:<{width}}  "
                f"{_fmt_qty(change.old_quantity)} -> {_fmt_qty(change.new_quantity)}"
            )
    else:
        print("  (none)")

    # --- New tickers ---
    print(f"\nNew tickers in export ({len(plan.additions)}):")
    if plan.additions:
        for pos in plan.additions:
            note = "" if add_new else "  — skipped (use --add-new to add)"
            print(
                f"  {pos.ticker}  qty {_fmt_qty(pos.quantity)}  [{pos.category}]{note}"
            )
    else:
        print("  (none)")

    # --- Informational: matched-but-unchanged and portfolio-only ---
    print(f"\nUnchanged (already correct): {len(plan.unchanged)}")
    if plan.portfolio_only:
        print(
            f"In portfolio but not in export "
            f"({len(plan.portfolio_only)}, left untouched): "
            + ", ".join(plan.portfolio_only)
        )
    print()


def run(
    export_file: str,
    portfolio: str | None = None,
    assume_yes: bool = False,
    dry_run: bool = False,
    add_new: bool = False,
) -> int:
    """Entry point for `portfolio import`. Returns a process exit code (0 = ok).

    Flow: build the plan → show it → (unless --dry-run) confirm → apply.
    """
    export_path = Path(export_file)
    if not export_path.exists():
        print(f"Error: export file not found: {export_path}")
        return 1

    # `--portfolio FILE` always means a local Excel file; otherwise use whichever
    # store STORAGE_BACKEND selects (Excel file or Google Sheet).
    try:
        store: PortfolioStore = (
            ExcelStore(Path(portfolio)) if portfolio else get_store(settings)
        )
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1
    # `isinstance` checks the store's actual class: only a file can be missing.
    if isinstance(store, ExcelStore) and not store.path.exists():
        print(f"Error: portfolio file not found: {store.path}")
        return 1

    try:
        # `asyncio.run` starts an event loop, runs the coroutine to completion,
        # and cleans up — the standard way to call async code from sync code.
        plan = asyncio.run(build_plan(export_path, store))
    # Catches problems with the B3 export *and* the portfolio store, because
    # `ExcelParseError` is a subclass of `StorageError` (see exceptions.py).
    except StorageError as exc:
        print(f"Error: {exc}")
        return 1

    _print_plan(plan, export_path, store.description, add_new)

    # Decide whether there is anything to do given the flags.
    will_add = add_new and bool(plan.additions)
    if not plan.updates and not will_add:
        print("Nothing to apply.")
        return 0

    if dry_run:
        print("Dry run — no changes written.")
        return 0

    # Confirm before writing, unless the user passed --yes for non-interactive use.
    if not assume_yes:
        answer = input("Apply these changes? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted — no changes written.")
            return 0

    try:
        asyncio.run(apply_plan(plan, store, add_new=add_new))
    except Exception as exc:
        print(f"Error while applying changes: {exc}")
        return 1

    added = len(plan.additions) if add_new else 0
    print(f"Done — {len(plan.updates)} updated, {added} added.")
    return 0
