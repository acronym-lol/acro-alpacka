// Express + Socket.io server for real-time event fan-out.
//
// Typical patterns this covers:
//   - Cue triggers — operator hits "go", every connected screen / prop
//     swaps in sync.
//   - Multi-screen sync — one browser fires an event, all others follow.
//   - Live dashboards — a producer publishes state, subscribers render.
//
// Room semantics: clients can `join`/`leave` arbitrary rooms; messages
// emitted to a room reach only clients in that room. Default setup
// treats every client as a member of the global broadcast.

import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import express from "express";
import { Server as IOServer } from "socket.io";

const HERE = dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.PORT ?? 4000);
const HOST = process.env.HOST ?? "0.0.0.0";
const CORS_ORIGIN = process.env.CORS_ORIGIN ?? "*";

// --- Express --------------------------------------------------------
const app = express();
app.use(express.json());

app.get("/health", (_req, res) => {
  res.json({ ok: true, clients: io?.engine.clientsCount ?? 0 });
});

// REST endpoint that broadcasts a cue to all connected clients. Useful
// when a trigger comes from somewhere outside Socket.io (TouchDesigner
// via HTTP, a webhook, a curl command).
//
//   curl -X POST http://localhost:4000/cue -H 'content-type: application/json' \
//        -d '{"name":"go","data":{"scene":"intro"}}'
app.post("/cue", (req, res) => {
  const { name, data, room } = req.body ?? {};
  if (!name) {
    res.status(400).json({ error: "missing 'name' field" });
    return;
  }
  const target = room ? io.to(room) : io;
  target.emit("cue", { name, data: data ?? null, ts: Date.now() });
  console.log(`[http] cue '${name}'${room ? ` → room ${room}` : ""}`);
  res.json({ ok: true });
});

// Static demo page at /.
const indexHtml = readFileSync(join(HERE, "public", "index.html"), "utf-8");
app.get("/", (_req, res) => {
  res.type("html").send(
    indexHtml.replaceAll("__PORT__", String(PORT))
  );
});

// --- Socket.io ------------------------------------------------------
const httpServer = createServer(app);
const io = new IOServer(httpServer, {
  cors: { origin: CORS_ORIGIN },
});

io.on("connection", (socket) => {
  console.log(`[ws] + ${socket.id} (${io.engine.clientsCount} total)`);
  socket.emit("welcome", { id: socket.id, ts: Date.now() });

  // Broadcast a join event so existing clients can track connection count.
  socket.broadcast.emit("peer:join", { id: socket.id });

  socket.on("join", (room) => {
    if (typeof room !== "string" || !room) return;
    socket.join(room);
    console.log(`[ws]   ${socket.id} joined room '${room}'`);
  });

  socket.on("leave", (room) => {
    if (typeof room !== "string" || !room) return;
    socket.leave(room);
    console.log(`[ws]   ${socket.id} left room '${room}'`);
  });

  // Client-originated cue — fan out to the same room set (or globally).
  socket.on("cue", ({ name, data, room } = {}) => {
    if (!name) return;
    const target = room ? socket.to(room) : socket.broadcast;
    target.emit("cue", { name, data: data ?? null, from: socket.id, ts: Date.now() });
    console.log(`[ws] cue '${name}' from ${socket.id}${room ? ` → room ${room}` : ""}`);
  });

  socket.on("disconnect", (reason) => {
    console.log(`[ws] - ${socket.id} (${reason})`);
    socket.broadcast.emit("peer:leave", { id: socket.id });
  });
});

// --- Lifecycle ------------------------------------------------------
httpServer.listen(PORT, HOST, () => {
  console.log(`[socketio] http://${HOST}:${PORT}`);
  console.log(`[socketio] demo page at http://localhost:${PORT}`);
});

function shutdown(reason) {
  console.log(`\n[socketio] shutting down (${reason})...`);
  io.close(() => httpServer.close(() => process.exit(0)));
}
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
