# {{PROJECT_NAME}}

Express + Socket.io server for real-time event fan-out — cue triggering,
multi-screen sync, live-show plumbing.

```
operator browser ──cue──▶  this server  ──cue──▶  all connected screens
                                ▲
                                │
   curl/TD HTTP POST /cue ──────┘
```

## Setup

```
npm install
```

## Run

Click Launch in ALPACKA, or:

```
npm run dev      # auto-reloads on change
npm start        # one-shot
```

Open the demo page (globe button on the card): `http://localhost:4000`.
Two browser tabs side-by-side show fan-out — fire a cue in one, it
lands in the other.

## Fire cues from anywhere

### From a browser (Socket.io client)

```js
const socket = io("http://localhost:4000");
socket.emit("cue", { name: "go", data: { scene: "intro" } });
```

### From curl / TouchDesigner / any HTTP source

```
curl -X POST http://localhost:4000/cue \
  -H 'content-type: application/json' \
  -d '{"name":"go","data":{"scene":"intro"}}'
```

TouchDesigner: drop in a `Web Client DAT` pointed at `/cue`, set method
to POST, feed it JSON bodies. Or use a `Python DAT` with `requests`.

### Scoped broadcasts (rooms)

Clients can subscribe to named "rooms" (stage-left, stage-right, front-of-house):

```js
socket.emit("join", "stage-left");
socket.emit("cue", { name: "blackout", room: "stage-left" });
```

`POST /cue` accepts an optional `"room"` field too.

## Event types

| Event | Direction | Payload |
|---|---|---|
| `welcome` | server → client | `{id, ts}` — sent to the connecting client |
| `peer:join` | server → clients | `{id}` — existing clients told a new one joined |
| `peer:leave` | server → clients | `{id}` — existing clients told one left |
| `cue` | client ↔ server | `{name, data, room?, ts, from?}` — the fan-out |
| `join` / `leave` | client → server | `"room-name"` — room membership |

## Tweaks

- **Lock CORS** for production — change `CORS_ORIGIN=*` to your allowed
  origins in `alpacka.yml`.
- **Add auth** — socket.io handshake supports middleware: check a token
  from `socket.handshake.auth.token` and call `next(new Error(...))` to
  reject.
- **Persist events** — wire in `docker-compose/redis` and use the
  `@socket.io/redis-adapter` for multi-process fan-out across a cluster.
- **Combine with OSC** — the `osc-websocket-bridge` template does the
  TD ↔ browser OSC leg; this template does the browser ↔ browser
  leg. Run both for a full show-control rig.
