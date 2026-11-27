import { createServer, request } from "node:http";
import { URL } from "node:url";

const upstreamBase = process.env.OPENHANDS_MODEL_SERVER_URL || "http://llama-server:8080";
const port = Number(process.env.PORT || 8080);
const pending = [];
let active = false;

function pump() {
  if (active || pending.length === 0) return;
  active = true;
  const job = pending.shift();
  if (job.cancelled) {
    active = false;
    pump();
    return;
  }
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    active = false;
    pump();
  };
  const upstream = request(new URL(job.req.url || "/", upstreamBase), {
    method: job.req.method,
    headers: job.req.headers,
  }, (response) => {
    job.res.writeHead(response.statusCode || 502, response.headers);
    response.pipe(job.res);
    response.once("end", finish);
    response.once("error", finish);
  });
  upstream.once("error", (error) => {
    if (!job.res.headersSent) {
      job.res.writeHead(502, { "Content-Type": "application/json" });
      job.res.end(JSON.stringify({ error: { message: `Model backend unavailable: ${error.message}` } }));
    } else job.res.destroy(error);
    finish();
  });
  job.res.once("close", () => {
    if (!job.res.writableEnded) upstream.destroy();
    finish();
  });
  job.req.pipe(upstream);
}

createServer((req, res) => {
  const job = { req, res, cancelled: false };
  req.once("aborted", () => { job.cancelled = true; });
  res.once("close", () => { if (pending.includes(job)) job.cancelled = true; });
  pending.push(job);
  pump();
}).listen(port, "0.0.0.0", () => {
  console.log(`OpenHands model queue listening on ${port}`);
});
