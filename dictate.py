"""Launcher script (the macOS .app's native launcher runs this file directly).

Equivalent to `python -m parakeet_dictate`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # embedded launcher doesn't add it

from parakeet_dictate.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
