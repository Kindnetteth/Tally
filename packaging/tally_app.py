"""Entry point PyInstaller freezes into Tally.app / Tally.exe."""

import sys

from tally.desktop import main

if __name__ == "__main__":
    sys.exit(main())
