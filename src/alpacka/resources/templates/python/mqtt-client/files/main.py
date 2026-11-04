"""MQTT client demo — subscribes to a topic, publishes a heartbeat.

Reads broker config from env (alpacka.yml's variables: section feeds
these in at launch time):
  MQTT_HOST, MQTT_PORT, MQTT_TOPIC, MQTT_PUBLISH_TOPIC,
  MQTT_USERNAME, MQTT_PASSWORD, HEARTBEAT_SECONDS.

Run via `python main.py` (or click Launch in ALPACKA).
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HOST = os.environ.get("MQTT_HOST", "localhost")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
SUB_TOPIC = os.environ.get("MQTT_TOPIC", "#")
PUB_TOPIC = os.environ.get("MQTT_PUBLISH_TOPIC", "heartbeat")
USERNAME = os.environ.get("MQTT_USERNAME", "")
PASSWORD = os.environ.get("MQTT_PASSWORD", "")
HEARTBEAT = int(os.environ.get("HEARTBEAT_SECONDS", "5"))


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[mqtt] connected to {HOST}:{PORT}")
        client.subscribe(SUB_TOPIC)
        print(f"[mqtt] subscribed to '{SUB_TOPIC}'")
    else:
        print(f"[mqtt] connect failed: {reason_code}", file=sys.stderr)


def on_disconnect(client, userdata, flags, reason_code, properties=None):
    print(f"[mqtt] disconnected (code={reason_code})")


def on_message(client, userdata, msg: mqtt.MQTTMessage):
    payload = msg.payload.decode("utf-8", errors="replace")
    print(f"[mqtt] {msg.topic} → {payload}")


# ---------------------------------------------------------------------------
# Client setup
# ---------------------------------------------------------------------------

client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id=f"alpacka-{os.getpid()}",
)
if USERNAME:
    client.username_pw_set(USERNAME, PASSWORD)
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message


def heartbeat_loop(stop_flag: threading.Event) -> None:
    """Publish a heartbeat payload every HEARTBEAT_SECONDS."""
    if HEARTBEAT <= 0:
        return
    while not stop_flag.wait(HEARTBEAT):
        payload = json.dumps({"ts": time.time(), "pid": os.getpid()})
        client.publish(PUB_TOPIC, payload)
        print(f"[mqtt] published → {PUB_TOPIC}")


def main() -> None:
    print(f"[mqtt] connecting to {HOST}:{PORT}...")
    client.connect(HOST, PORT, keepalive=60)

    stop = threading.Event()
    heartbeat = threading.Thread(target=heartbeat_loop, args=(stop,), daemon=True)
    heartbeat.start()

    # Graceful Ctrl-C / SIGTERM.
    def _shutdown(_signum, _frame):
        print("\n[mqtt] shutting down...")
        stop.set()
        client.disconnect()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    client.loop_forever()


if __name__ == "__main__":
    main()
