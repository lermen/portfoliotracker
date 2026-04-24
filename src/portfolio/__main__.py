# `__main__.py` is a special Python file. When you run a package with:
#   python -m portfolio
# Python executes this file. It is the standard entry point for runnable packages.

from portfolio.ui.tui.app import PortfolioApp


def main() -> None:
    # `.run()` starts the Textual event loop, which blocks until the user quits.
    PortfolioApp().run()


# `if __name__ == "__main__"` is another Python convention:
# this block runs ONLY when the script is executed directly (not when imported).
# It prevents `main()` from being called accidentally when another module imports
# from this file.
if __name__ == "__main__":
    main()
