"""PyInstaller entry point for the packaged PawFlow Relay client."""

from pawflow_relay.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main() or 0)

