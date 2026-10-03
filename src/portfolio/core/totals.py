# This module holds the arithmetic for aggregating a group of positions:
# reconstructing past values from percentage changes, and the overall
# unrealised P&L of the group.
#
# It used to live inline in the TUI (twice — once for the status bar and once
# for the Summary tab). Pulling it into `core/` means:
#   - both call sites share one implementation, so they can't drift apart;
#   - it is plain functions over plain data, so it can be unit-tested without
#     starting the Textual app;
#   - any future UI (e.g. the web frontend) can reuse it.
#
# Every function takes "a group of positions" as input — the whole portfolio,
# a filtered subset, or the rows the user has selected. The math doesn't care.

from collections.abc import Iterable

from portfolio.core.models import PositionValue


def value_before(value_brl: float, change_pct: float | None) -> float:
    """Back-calculate a value before a given percentage change.

    If today's value = V and the change was p%, then:
        V_before = V / (1 + p/100)

    A missing change (`None`) means "no data", so the value is returned as-is.
    A -100% change (price fell to zero) would divide by zero; the prior value is
    unrecoverable from a current value of zero, so it is also returned unchanged.
    """
    if change_pct is None:
        return value_brl
    denom = 1.0 + change_pct / 100.0
    if denom == 0:
        return value_brl
    return value_brl / denom


def cost_and_value(positions: Iterable[PositionValue]) -> tuple[float, float]:
    """Return `(total_cost_brl, total_value_brl)` for positions with a known cost.

    The BRL cost basis of each position is reconstructed from `value_brl` and
    `pnl_pct`:
        pnl_pct/100 = (value − cost) / cost  ⇒  cost = value / (1 + pnl_pct/100)

    This identity holds in both currency conventions used by the engine
    (native-currency avg for stocks/ETFs, BRL avg for crypto), because the
    engine always computes pnl_pct against a same-currency reference price.

    Positions are skipped (they count on neither side of the ratio) when:
      - they have no avg price (`pnl_pct is None`), or
      - `pnl_pct == -100` (a live price of zero), where the cost is undefined.

    `Iterable[PositionValue]` accepts a list, a tuple, a generator — anything
    you can loop over once.
    """
    total_cost = 0.0
    total_value = 0.0
    for pv in positions:
        if pv.pnl_pct is None:
            continue
        denom = 1.0 + pv.pnl_pct / 100.0
        if denom == 0:
            continue
        total_cost += pv.value_brl / denom
        total_value += pv.value_brl
    return total_cost, total_value


def total_pnl_pct(positions: Iterable[PositionValue]) -> float | None:
    """Overall unrealised P&L % of a group of positions, or None if unknown.

    `None` means no position in the group has a usable cost basis, so there is
    nothing to compare against (the UI shows "N/A").
    """
    cost, value = cost_and_value(positions)
    if cost <= 0:
        return None
    return (value - cost) / cost * 100.0
