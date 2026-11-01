"""Set Tally's version before a build.

    python packaging/set_version.py 1.1.0

The release workflow runs this with the release's tag, so the app's About
line and its Info.plist show the version you released.
"""

import re
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parent.parent / "tally" / "__init__.py"


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+(\.\d+){0,3}", sys.argv[1]):
        sys.exit("usage: set_version.py 1.2.3")
    text = INIT.read_text(encoding="utf-8")
    INIT.write_text(re.sub(r'__version__ = "[^"]*"', f'__version__ = "{sys.argv[1]}"', text), encoding="utf-8")
    print(f"version {sys.argv[1]}")


if __name__ == "__main__":
    main()
