# {{PROJECT_NAME}}

Eclipse Mosquitto MQTT broker — local dev environment.

```
TouchDesigner / browser / Python / etc.  ─MQTT─▶  Mosquitto  ─MQTT─▶  subscribers
```

## Run

Click Launch in ALPACKA (runs `docker compose up`). Stop via the red
button on the project card or `docker compose down`.

```
docker compose up -d
docker compose logs -f
docker compose down       # volumes persist
docker compose down -v    # also wipe broker state
```

## Connect

| Transport | URL | Use from |
|---|---|---|
| MQTT (TCP) | `tcp://localhost:1883` | Python, Node, TouchDesigner MQTT DAT, ESP32, etc. |
| MQTT (WebSocket) | `ws://localhost:9001` | Browsers (mqtt.js with `ws://` transport) |

Pair with the **python/mqtt-client** or **nodejs/mqtt-client** templates
for a turnkey publisher/subscriber starter.

Quick sanity from the command line:

```
# Subscribe (in one terminal)
docker compose exec mosquitto mosquitto_sub -h localhost -t '#' -v

# Publish (in another)
docker compose exec mosquitto mosquitto_pub -h localhost -t test/topic -m hello
```

## TouchDesigner

Drop in an `MQTT DAT`. Set:
- Network address: `127.0.0.1` (or this machine's LAN IP from another machine)
- Network port: `1883`
- Client name: anything unique per TD instance

Subscribe via the DAT's parameters; publish via `op('mqtt_dat').publish('topic', 'payload')`.

## Locking down

The default config allows anonymous connections — fine for laptop dev,
not for anything on a venue Wi-Fi. To require auth:

1. Edit `config/mosquitto.conf`:
   ```
   allow_anonymous false
   password_file /mosquitto/config/passwd
   ```
2. Generate a password file:
   ```
   docker compose exec mosquitto mosquitto_passwd -c /mosquitto/config/passwd alpacka
   ```
3. Restart: `docker compose restart`.

For TLS, mount certs at `/mosquitto/config/certs/` and add `cafile` /
`certfile` / `keyfile` directives to the listener block.
