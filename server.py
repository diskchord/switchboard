from __future__ import annotations

import argparse

from texting_app.server import run


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the local Switchboard server.")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    run(host=args.host, port=args.port)
