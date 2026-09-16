"""API entry point with a psycopg-compatible event loop on Windows."""
import asyncio

import uvicorn


def main():
    config = uvicorn.Config("backend.main:app", host="127.0.0.1", port=8000)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
