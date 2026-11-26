import argparse
import os

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Start the local QuantumNest dashboard.")
    parser.add_argument("--host", default=os.getenv("UVICORN_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT") or os.getenv("UVICORN_PORT", "8765")))
    parser.add_argument("--strictPort", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    uvicorn.run("backend.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
