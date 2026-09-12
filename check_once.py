"""
Runs a single signal check and exits.
Used by GitHub Actions, which runs this on a schedule instead of
keeping a process alive continuously.
"""

from main import run_once

if __name__ == "__main__":
    run_once()
