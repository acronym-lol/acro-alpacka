# {{PROJECT_NAME}}

MQTT client (paho-mqtt). Subscribes to a topic pattern and publishes
heartbeat messages.

## Setup

```
python -m venv .venv
.venv/Scripts/activate         # Windows
source .venv/bin/activate      # macOS / Linux
pip install -e .
```

## Run

Click Launch in ALPACKA, or:

```
python main.py
```

You'll see connect / subscribe / received-message logs in the terminal.

## Pair with the broker

Spin up `docker-compose/mqtt-broker` first, then this — they default to
each other (`localhost:1883`).

## Test from the command line

```
# Trigger a message that this client picks up:
mosquitto_pub -h localhost -t "{{PROJECT_NAME}}/test" -m "hello"
```

(Or use the broker's container: `docker compose exec mosquitto mosquitto_pub -h localhost -t '...' -m '...'`.)

## Customizing

- Wildcards in `MQTT_TOPIC` work as expected: `+` (single segment) and
  `#` (multi-level).
- Replace `on_message` with your own routing — match `msg.topic` and
  dispatch.
- For TLS broker, switch `client.connect` for `client.connect_tls` and
  point `MQTT_PORT` at 8883.
