"""Associate an existing launcher shortcut with the running LRE application."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from windows_identity import configure_shortcut_identity, read_taskbar_identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("shortcut", type=Path)
    args = parser.parse_args()
    if args.shortcut.suffix.lower() != ".lnk" or not args.shortcut.is_file():
        parser.error("An existing .lnk file is required")
    configure_shortcut_identity(args.shortcut, ROOT)
    print(f"Taskbar identity: {read_taskbar_identity(path=args.shortcut)['app_id']}")


if __name__ == "__main__":
    main()
