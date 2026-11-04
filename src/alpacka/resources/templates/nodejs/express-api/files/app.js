import "dotenv/config";
import cors from "cors";
import express from "express";

import helloRouter from "./routes/hello.js";

const app = express();
app.use(cors());
app.use(express.json());

app.use("/hello", helloRouter);

app.get("/", (_req, res) => {
  res.json({ ok: true, service: process.env.npm_package_name ?? "app" });
});

const PORT = Number(process.env.PORT ?? 3000);
const HOST = process.env.HOST ?? "localhost";

app.listen(PORT, HOST, () => {
  console.log(`listening on http://${HOST}:${PORT}`);
});
