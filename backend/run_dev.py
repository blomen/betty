"""
Dev server launcher.

Usage:
    python run_dev.py
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "src.api:app",
        host="127.0.0.1",
        port=8000,
        timeout_keep_alive=120,
        reload=True,
    )
