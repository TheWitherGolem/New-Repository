"""Entry point: python -m aloud"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="aloud",
        description="Read highlighted text aloud from anywhere, via a hotkey.")
    parser.add_argument("--verbose", action="store_true",
                        help="also log to the console")
    parser.add_argument("--show", action="store_true",
                        help="open the control window at startup")
    parser.add_argument("--version", action="store_true",
                        help="print the version and exit")
    args = parser.parse_args()

    from . import __version__

    if args.version:
        print(f"Aloud {__version__}")
        return 0

    from .app import AloudApp, setup_logging

    setup_logging(verbose=args.verbose)
    app = AloudApp()
    if args.show:
        app.config.start_hidden = False
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
