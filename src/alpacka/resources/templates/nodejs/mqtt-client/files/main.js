// mqtt.js client demo — subscribe + heartbeat publish.
// Reads broker config from env (alpacka.yml's variables: section).

import mqtt from "mqtt";

const HOST = process.env.MQTT_HOST ?? "localhost";
const PORT = Number(process.env.MQTT_PORT ?? 1883);
const SUB_TOPIC = process.env.MQTT_TOPIC ?? "#";
const PUB_TOPIC = process.env.MQTT_PUBLISH_TOPIC ?? "heartbeat";
const USERNAME = process.env.MQTT_USERNAME ?? "";
const PASSWORD = process.env.MQTT_PASSWORD ?? "";
const HEARTBEAT = Number(process.env.HEARTBEAT_SECONDS ?? 5);

const url = `mqtt://${HOST}:${PORT}`;
const opts = {
  clientId: `alpacka-${process.pid}`,
  reconnectPeriod: 2000,
  ...(USERNAME ? { username: USERNAME, password: PASSWORD } : {}),
};

console.log(`[mqtt] connecting to ${url}...`);
const client = mqtt.connect(url, opts);

client.on("connect", () => {
  console.log(`[mqtt] connected`);
  client.subscribe(SUB_TOPIC, (err) => {
    if (err) {
      console.error(`[mqtt] subscribe failed:`, err);
      return;
    }
    console.log(`[mqtt] subscribed to '${SUB_TOPIC}'`);
  });
});

client.on("error", (err) => {
  console.error(`[mqtt] error:`, err.message);
});

client.on("disconnect", () => {
  console.log(`[mqtt] disconnected`);
});

client.on("reconnect", () => {
  console.log(`[mqtt] reconnecting...`);
});

client.on("message", (topic, payload) => {
  console.log(`[mqtt] ${topic} → ${payload.toString()}`);
});

if (HEARTBEAT > 0) {
  setInterval(() => {
    if (!client.connected) return;
    const body = JSON.stringify({ ts: Date.now(), pid: process.pid });
    client.publish(PUB_TOPIC, body);
    console.log(`[mqtt] published → ${PUB_TOPIC}`);
  }, HEARTBEAT * 1000);
}

// Graceful shutdown so Stop on the project card disconnects cleanly.
function shutdown(reason) {
  console.log(`\n[mqtt] shutting down (${reason})...`);
  client.end(false, () => process.exit(0));
}
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
