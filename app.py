"""Desktop launcher for the native Qt interface; --legacy restores the old UI."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main():
    # Frozen worker entry: WorkBuddyManager.exe --mode poll runs the backend, not the UI.
    if "--mode" in sys.argv:
        from runner import main as work
        return work()
    if "--legacy" in sys.argv:
        sys.argv.remove("--legacy")
        from legacy_tk_app import main as launch
    else:
        from qt_app import main as launch
    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
