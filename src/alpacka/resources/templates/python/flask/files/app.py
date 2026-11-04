"""Flask app — launched as `python app.py`. PORT, HOST, FLASK_DEBUG come
from alpacka.yml's variables (injected as env vars at launch time)."""
from __future__ import annotations

import os

from flask import Flask, jsonify, request

app = Flask(__name__)


@app.get("/")
def root():
    return jsonify(ok=True, service=app.import_name)


@app.get("/hello/<name>")
def hello(name: str):
    return jsonify(message=f"hello, {name}")


@app.post("/webhook")
def webhook():
    """Example webhook receiver — echoes payload + headers back as JSON."""
    return jsonify(
        headers={k: v for k, v in request.headers.items()},
        json=request.get_json(silent=True),
        form=request.form.to_dict(),
    )


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1").lower() in ("1", "true", "yes", "on")
    app.run(host=host, port=port, debug=debug)
