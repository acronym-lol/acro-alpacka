# {{PROJECT_NAME}}

MQTT client (mqtt.js). Subscribes to a topic pattern and publishes
heartbeat messages.

## Setup

```
npm install
```

## Run

Click Launch in ALPACKA, or:

```
npm run dev      # auto-reloads on file change
npm start        # one-shot
```

You'll see connect / subscribe / received-message logs in the terminal.

## Pair with the broker

Spin up `docker-compose/mqtt-broker` first, then this — they default to
each other (`localhost:1883`).

## Test from the command line

```
mosquitto_pub -h localhost -t "{{PROJECT_NAME}}/test" -m "hello"
```

(Or use the broker's container: `docker compose exec mosquitto mosquitto_pub -h localhost -t '...' -m '...'`.)

## Browser variant

For mqtt.js in a browser, change the URL scheme to `ws://localhost:9001`
(MQTT over WebSocket) — that's the second port the broker template
exposes. Otherwise the API is identical.

## Customizing

- Wildcards in `MQTT_TOPIC` work as expected: `+` (single segment),
  `#` (multi-level).
- Replace the `client.on("message", ...)` handler with your own routing
  — match `topic` and dispatch.
- For TLS broker, switch the URL scheme to `mqtts://` and set port 8883.
