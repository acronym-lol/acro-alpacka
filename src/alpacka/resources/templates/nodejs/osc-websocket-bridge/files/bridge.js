// OSC ↔ WebSocket bridge.
//
// Wire diagram:
//
//   TD / Resolume / Notch ---UDP OSC--->  this process  --WS msgs-->  browser
//                       <---UDP OSC---           <--WS msgs--
//
// All ports + host are read from env vars (alpacka.yml's variables:
// section feeds them at launch time).

import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import osc from "osc";
import { WebSocketServer } from "ws";

const HERE = dirname(fileURLToPath(import.meta.url));

const OSC_IN_PORT = Number(process.env.OSC_IN_PORT ?? 7400);
const OSC_OUT_HOST = process.env.OSC_OUT_HOST ?? "127.0.0.1";
const OSC_OUT_PORT = Number(process.env.OSC_OUT_PORT ?? 7401);
const WS_PORT = Number(process.env.WS_PORT ?? 8080);
const HTTP_PORT = Number(process.env.PORT ?? 3000);
const HTTP_HOST = process.env.HOST ?? "localhost";

// --- OSC UDP socket --------------------------------------------------
const oscPort = new osc.UDPPort({
  localAddress: "0.0.0.0",
  localPort: OSC_IN_PORT,
  remoteAddress: OSC_OUT_HOST,
  remotePort: OSC_OUT_PORT,
});

oscPort.on("ready", () => {
  console.log(`[osc] listening on udp://0.0.0.0:${OSC_IN_PORT}`);
  console.log(`[osc] outbound → udp://${OSC_OUT_HOST}:${OSC_OUT_PORT}`);
});

oscPort.on("error", (err) => {
  console.error("[osc] error:", err.message);
});

oscPort.open();

// --- WebSocket server ------------------------------------------------
const wss = new WebSocketServer({ port: WS_PORT });

wss.on("listening", () => {
  console.log(`[ws]  ws://localhost:${WS_PORT}`);
});

wss.on("connection", (socket) => {
  console.log(`[ws]  client connected (total: ${wss.clients.size})`);

  socket.on("message", (raw) => {
    // Browser → OSC: expect JSON `{ address: "/foo", args: [1, 2.0, "x"] }`.
    let payload;
    try {
      payload = JSON.parse(raw.toString());
    } catch (err) {
      console.warn("[ws] non-JSON message dropped:", raw.toString().slice(0, 120));
      return;
    }
    if (!payload || typeof payload.address !== "string") {
      console.warn("[ws] message missing address field; dropped");
      return;
    }
    try {
      oscPort.send({
        address: payload.address,
        args: Array.isArray(payload.args) ? payload.args : [],
      });
    } catch (err) {
      console.error("[ws→osc] send failed:", err.message);
    }
  });

  socket.on("close", () => {
    console.log(`[ws]  client disconnected (total: ${wss.clients.size})`);
  });
});

// OSC → all WS clients.
oscPort.on("message", (msg) => {
  const json = JSON.stringify(msg);
  for (const client of wss.clients) {
    if (client.readyState === client.OPEN) {
      client.send(json);
    }
  }
});

// --- Demo HTTP server (serves public/) -------------------------------
const indexHtml = readFileSync(join(HERE, "public", "index.html"), "utf-8");

const http = createServer((req, res) => {
  if (req.url === "/" || req.url === "/index.html") {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(
      indexHtml
        .replaceAll("__WS_PORT__", String(WS_PORT))
        .replaceAll("__OSC_IN_PORT__", String(OSC_IN_PORT))
        .replaceAll("__OSC_OUT__", `${OSC_OUT_HOST}:${OSC_OUT_PORT}`)
    );
    return;
  }
  res.writeHead(404, { "content-type": "text/plain" });
  res.end("not found");
});

http.listen(HTTP_PORT, HTTP_HOST, () => {
  console.log(`[http] http://${HTTP_HOST}:${HTTP_PORT}  (demo page)`);
});
