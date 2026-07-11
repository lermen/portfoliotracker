# The `cli` package is a command-line frontend for the portfolio tracker, a sibling
# of the `tui` (Textual) frontend. Like every UI in this project it consumes the
# `core` layer and never the other way around: it prints and prompts, while all the
# real work (parsing, diffing, writing) lives in `portfolio.core`.
