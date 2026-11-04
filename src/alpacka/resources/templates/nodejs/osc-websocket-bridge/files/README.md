# {{PROJECT_NAME}}

OSC ↔ WebSocket bridge — lets a browser UI talk to TouchDesigner /
Resolume / Notch / Ableton (or anything that speaks OSC).

```
TD / Resolume / etc. ──UDP OSC──▶  this bridge  ──WebSocket──▶  browser
                     ◀──UDP OSC──            ◀──WebSocket──
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

Open the demo page (ALPACKA's globe button on the project card jumps
there): `http://localhost:3000`. You'll see live OSC traffic in real time
and can fire test messages back out.

## Wiring up senders

### TouchDesigner

Drop an `OSCOUT CHOP` (or DAT) — set `Network Address` to wherever the
bridge is running (`127.0.0.1` for the same machine), `Network Port` to
**OSC_IN_PORT** (default `7400`).

### Resolume

Preferences → OSC → Output → host `127.0.0.1`, port `7400`.

### Anything else

Just point its OSC output at `udp://<bridge-host>:7400`.

## Receiving in the browser

```js
const ws = new WebSocket("ws://localhost:8080");
ws.onmessage = (ev) => {
  const { address, args } = JSON.parse(ev.data);
  console.log(address, args);
};
```

## Sending OSC from the browser

```js
ws.send(JSON.stringify({ address: "/cue/next", args: [] }));
ws.send(JSON.stringify({ address: "/fader/main", args: [0.75] }));
```

The bridge forwards these to **OSC_OUT_HOST:OSC_OUT_PORT** (defaults
`127.0.0.1:7401`). Listen on that port from TD/etc. to round-trip.

## Tweaks

All ports + hosts come from `alpacka.yml`'s `variables:` section — edit
in ALPACKA's Edit Details page. Common tweaks:

- TD round-trip on the same machine: `OSC_IN_PORT=7400`, `OSC_OUT_PORT=7401`
- Bridge to a remote TD: set `OSC_OUT_HOST` to the TD machine's IP
- Multi-client (tablets on a venue Wi-Fi): leave `WS_PORT` open, point
  every browser at the bridge's LAN IP
