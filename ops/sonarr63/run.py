"""Pinned host bundle entrypoint. Invoke with /usr/bin/python3 -I -B."""
from pathlib import Path
import sys

# Only an explicitly operator-installed, hash-reviewed sibling bundle is loaded.
# No working-directory/PYTHONPATH package or production checkout dependency.
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from personal_ai_brain.sonarr_snapshot_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
