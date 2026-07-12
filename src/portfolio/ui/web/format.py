# Formatting and color helpers shared by every page.
#
# These port the conventions from the design handoff (`app.jsx`'s
# `classPalette`/`colorForPosition`, `data.jsx`'s `fmtBRL`/`fmtPct`) and from
# `ui/tui/widgets.py`'s `_compact_brl`, adapted to pt-BR number formatting as
# the handoff's README specifies.

# The design assumes a fixed 4-class taxonomy (stock/etf/crypto/fixed) for
# palette and glyph lookups, but `PositionValue.category` in this app is a
# free-text field the user types into the spreadsheet (e.g. "FII", "Ações").
# `normalize_class` maps the free text onto the design's 4 buckets by keyword
# so the palette/glyph still apply; anything unrecognized falls back to "stock".
_CLASS_KEYWORDS: dict[str, str] = {
    "crypto": "crypto",
    "bitcoin": "crypto",
    "etf": "etf",
    "fii": "etf",
    "fund": "etf",
    "fixed": "fixed",
    "renda fixa": "fixed",
    "bond": "fixed",
    "tesouro": "fixed",
    "cdb": "fixed",
    "debenture": "fixed",
    "debênture": "fixed",
}

CLASS_PALETTE: dict[str, list[str]] = {
    "stock": ["#10b981", "#34d399", "#6ee7b7"],
    "etf": ["#3b82f6", "#60a5fa", "#93c5fd"],
    "crypto": ["#f59e0b", "#fbbf24", "#fcd34d"],
    "fixed": ["#8b5cf6", "#a78bfa", "#c4b5fd"],
}

CLASS_GLYPH: dict[str, str] = {
    "stock": "▲",
    "etf": "■",
    "crypto": "◆",
    "fixed": "●",
}

UP_COLOR = "#10b981"
DOWN_COLOR = "#ef4444"


def normalize_class(category: str) -> str:
    """Map a free-text category to one of the design's 4 asset classes."""
    lowered = category.lower()
    for keyword, cls in _CLASS_KEYWORDS.items():
        if keyword in lowered:
            return cls
    return "stock"


def color_for_position(ticker: str, category: str) -> str:
    """Pick a shade for `ticker` from its class's palette, hashed for variety.

    Mirrors `colorForPosition` in the handoff's `app.jsx`: a simple string
    hash (`h = h*31 + charCode`) selects a stable shade per ticker so the
    same position always renders the same color across charts.
    """
    shades = CLASS_PALETTE[normalize_class(category)]
    h = 0
    for ch in ticker:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return shades[h % len(shades)]


def fmt_brl(value: float, hide: bool = False, sign: bool = False) -> str:
    """Format a BRL amount using pt-BR conventions (comma decimal, period thousands)."""
    if hide:
        return "R$ ••••••"
    prefix = "-" if value < 0 else ("+" if sign else "")
    # `:,.2f` gives US grouping (1,234.56); swap separators to pt-BR (1.234,56).
    grouped = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{prefix}R$ {grouped}"


def fmt_compact_brl(value: float, hide: bool = False) -> str:
    """Compact BRL for tight spaces: R$1,2M / R$328k / R$540."""
    if hide:
        return "R$ •••"
    sign = "-" if value < 0 else ""
    abs_value = abs(value)
    if abs_value >= 1_000_000:
        return f"{sign}R${abs_value / 1_000_000:.1f}M".replace(".", ",")
    if abs_value >= 1_000:
        return f"{sign}R${abs_value / 1_000:.0f}k"
    return f"{sign}R${abs_value:.0f}"


def fmt_pct(value: float | None, sign: bool = True) -> str:
    """Format a percentage; percentages stay visible even in privacy mode."""
    if value is None:
        return "N/A"
    prefix = "+" if sign and value >= 0 else ""
    return f"{prefix}{value:.2f}%"


def fmt_quantity(quantity: float, category: str) -> str:
    """More decimals for fractional crypto amounts, matching the TUI's formatting."""
    if normalize_class(category) == "crypto":
        return f"{quantity:,.8f}" if quantity % 1 else f"{quantity:,.0f}"
    return f"{quantity:,.2f}" if quantity % 1 else f"{quantity:,.0f}"


def change_color(value: float | None) -> str:
    """CSS color for a signed change: up/down/neutral."""
    if value is None:
        return "#5a6477"
    return UP_COLOR if value >= 0 else DOWN_COLOR
