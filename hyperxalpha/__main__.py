import argparse
from .settings import load_settings


def main():
    parser = argparse.ArgumentParser(description="HyperX Alpha Python app")
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Disable system tray integration",
    )
    parser.add_argument(
        "--start-hidden",
        action="store_true",
        help="Start hidden when tray is available",
    )
    args = parser.parse_args()

    from .ui import run

    settings = load_settings()
    start_hidden = args.start_hidden or settings.start_hidden
    run(start_hidden=start_hidden, use_tray=not args.no_tray)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
