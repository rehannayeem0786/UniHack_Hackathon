"""Start the enrichment service.

Serves the API and, when it has been built, the dashboard bundle from the same
origin. Bound to localhost by default: the service has no authentication, so
exposing it on a network interface would let anyone submit work and read every
result. Pass --host deliberately if that is what you want.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from backend.config import PROJECT_ROOT, settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true", help="restart on code changes")
    args = parser.parse_args()

    built = (PROJECT_ROOT / "frontend" / "dist" / "index.html").exists()

    print("=" * 62)
    print(" UniHack 2026 - Product Intelligence service")
    print("=" * 62)
    print(f"  API      http://{args.host}:{args.port}/docs")
    if built:
        print(f"  Dashboard http://{args.host}:{args.port}/")
    else:
        print("  Dashboard not built - run: cd frontend && npm install && npm run build")
    if args.host not in {"127.0.0.1", "localhost"}:
        print("\n  WARNING: this service has no authentication and you have bound it")
        print(f"           to {args.host}. Anyone who can reach it can use it.")
    print()

    uvicorn.run(
        "backend.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
