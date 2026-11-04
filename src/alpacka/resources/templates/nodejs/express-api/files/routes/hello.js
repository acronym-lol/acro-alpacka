import { Router } from "express";

const router = Router();

router.get("/", (_req, res) => {
  res.json({ message: "hello" });
});

router.get("/:name", (req, res) => {
  res.json({ message: `hello, ${req.params.name}` });
});

export default router;
