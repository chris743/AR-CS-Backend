"""Launch the billing UI from any working directory.

    venv/bin/python standalone/serve.py            # http://127.0.0.1:8100
    venv/bin/python standalone/serve.py 9000        # custom port

Resolves its own location, so it doesn't matter where you run it from.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from standalone.server import app  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8100
    # 0.0.0.0 so the frontend on another host (e.g. 10.0.1.105) can reach the API,
    # not just localhost. CORS for that origin is configured in server.py.
    uvicorn.run(app, host="0.0.0.0", port=port)
