"""`python -m pais_mock` — run the mock PAIS HTTP server."""

from __future__ import annotations

import argparse

import uvicorn

from pais_mock.server import build_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="pais_mock")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--seed", type=str, default=None, help="JSON seed file")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    app = build_app(seed=args.seed)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
