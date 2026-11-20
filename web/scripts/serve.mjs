// Local static server for out/ that applies the same headers as vercel.json,
// so the CSP is exercised before deploying.  node scripts/serve.mjs [port]
import { createServer } from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { extname, join, normalize, resolve } from "node:path";

const web = resolve(import.meta.dirname, "..");
const out = join(web, "out");
const port = Number(process.argv[2] ?? 4317);
const headers = JSON.parse(readFileSync(join(web, "vercel.json"), "utf8")).headers[0].headers;
const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".py": "text/x-python", ".svg": "image/svg+xml", ".ico": "image/x-icon",
  ".woff2": "font/woff2", ".txt": "text/plain", ".wasm": "application/wasm", ".zip": "application/zip",
};

createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  let p = normalize(join(out, decodeURIComponent(url.pathname)));
  if (!p.startsWith(out)) return res.writeHead(400).end();
  if (existsSync(p) && statSync(p).isDirectory()) p = join(p, "index.html");
  if (!existsSync(p)) {
    res.writeHead(404, { "content-type": "text/html" });
    return res.end(existsSync(join(out, "404.html")) ? readFileSync(join(out, "404.html")) : "not found");
  }
  for (const h of headers) res.setHeader(h.key, h.value);
  res.setHeader("content-type", TYPES[extname(p)] ?? "application/octet-stream");
  res.end(readFileSync(p));
}).listen(port, () => console.log(`serving out/ with vercel.json headers on http://localhost:${port}`));
