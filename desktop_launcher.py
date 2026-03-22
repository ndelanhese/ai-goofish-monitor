"""
Desktop launch entry point.
When packaged with PyInstaller as a single executable, this module automatically
starts the FastAPI service and opens a browser window.
"""
import os
import sys
import time
import webbrowser
from pathlib import Path

import uvicorn

# PyInstaller runtime resource directory: _MEIPASS; falls back to the directory containing this file when not packaged
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _prepare_environment() -> None:
    """Ensure the working directory and module paths are correct."""
    os.chdir(BASE_DIR)
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))


def run_app() -> None:
    """Start the FastAPI application and automatically open a browser."""
    _prepare_environment()

    from src.app import app
    from src.infrastructure.config.settings import settings

    # Open the browser first, then wait briefly for the service to start
    url = f"http://127.0.0.1:{settings.server_port}"
    webbrowser.open(url)
    time.sleep(0.5)

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=settings.server_port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    run_app()
