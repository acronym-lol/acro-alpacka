"""FastAPI app — launched as `python main.py` which bootstraps uvicorn.

Can also be run via `uvicorn main:app --reload` directly if you prefer
driving uvicorn yourself.
"""
from __future__ import annotations

import os

from fastapi import FastAPI

app = FastAPI(title=os.environ.get("npm_package_name", "service"))


@app.get("/")
def root() -> dict[str, bool | str]:
    return {"ok": True, "service": app.title}


@app.get("/hello/{name}")
def hello(name: str) -> dict[str, str]:
    return {"message": f"hello, {name}"}


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "1").lower() in ("1", "true", "yes", "on")
    uvicorn.run("main:app", host=host, port=port, reload=reload)
