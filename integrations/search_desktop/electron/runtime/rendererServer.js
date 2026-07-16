import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { extname, join, normalize, resolve, sep } from "node:path";

const MIME = Object.freeze({
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
});

export class RendererServer {
  constructor({ frontendDist, fallbackFile, designSystemRoot, rendererAssets, backendUrl, host = "127.0.0.1", port = 5173 }) {
    this.frontendDist = resolve(frontendDist);
    this.fallbackFile = resolve(fallbackFile);
    this.designSystemRoot = designSystemRoot ? resolve(designSystemRoot) : null;
    this.rendererAssets = rendererAssets ? resolve(rendererAssets) : null;
    this.backendUrl = new URL(backendUrl);
    this.host = host;
    this.port = port;
    this.server = null;
    this.origin = null;
  }

  async start() {
    if (this.server) return this.origin;
    this.server = createServer((request, response) => this.handle(request, response));
    try {
      await new Promise((resolvePromise, reject) => {
        this.server.once("error", reject);
        this.server.listen(this.port, this.host, () => resolvePromise());
      });
    } catch (error) {
      this.server = null;
      throw new Error(error?.code === "EADDRINUSE" ? "renderer_port_conflict" : "renderer_server_start_failed");
    }
    const address = this.server.address();
    if (!address || typeof address === "string") throw new Error("renderer_server_address_invalid");
    this.origin = `http://${this.host}:${address.port}`;
    return this.origin;
  }

  async stop() {
    if (!this.server) return;
    const server = this.server;
    this.server = null;
    this.origin = null;
    await new Promise((resolvePromise) => server.close(() => resolvePromise()));
  }

  handle(request, response) {
    setSecurityHeaders(response, this.backendUrl.origin);
    const url = new URL(request.url || "/", `http://${this.host}`);
    if (url.pathname.startsWith("/api/")) {
      this.proxyApi(request, response, url);
      return;
    }
    if (url.pathname.startsWith("/__search_design__/") && this.designSystemRoot) {
      const asset = safeAssetPath(
        this.designSystemRoot,
        url.pathname.slice("/__search_design__".length),
      );
      if (asset && existsSync(asset) && statSync(asset).isFile()) {
        serveFile(asset, response);
        return;
      }
    }
    if (url.pathname.startsWith("/__search_desktop__/") && this.rendererAssets) {
      const asset = safeAssetPath(
        this.rendererAssets,
        url.pathname.slice("/__search_desktop__".length),
      );
      if (asset && existsSync(asset) && statSync(asset).isFile()) {
        serveFile(asset, response);
        return;
      }
    }
    if (!existsSync(join(this.frontendDist, "index.html"))) {
      serveFile(this.fallbackFile, response, 503);
      return;
    }
    const requested = safeAssetPath(this.frontendDist, url.pathname);
    if (requested && existsSync(requested) && statSync(requested).isFile()) {
      if (requested === join(this.frontendDist, "index.html")) {
        serveFrontendIndex(requested, response);
      } else {
        serveFile(requested, response);
      }
      return;
    }
    serveFrontendIndex(join(this.frontendDist, "index.html"), response);
  }

  proxyApi(request, response, url) {
    const method = String(request.method || "GET").toUpperCase();
    if (!["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"].includes(method)) {
      response.writeHead(405, { "content-type": "application/json; charset=utf-8" });
      response.end(JSON.stringify({ status: "error", error_code: "method_not_allowed" }));
      return;
    }
    const upstream = httpRequest(
      {
        protocol: this.backendUrl.protocol,
        hostname: this.backendUrl.hostname,
        port: this.backendUrl.port,
        method,
        path: `${url.pathname}${url.search}`,
        headers: filteredHeaders(request.headers, this.backendUrl.host),
      },
      (upstreamResponse) => {
        response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
        upstreamResponse.pipe(response);
      },
    );
    upstream.on("error", () => {
      if (!response.headersSent) response.writeHead(502, { "content-type": "application/json; charset=utf-8" });
      response.end(JSON.stringify({ status: "error", error_code: "backend_unavailable" }));
    });
    request.pipe(upstream);
  }
}

function safeAssetPath(root, pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  const normalized = normalize(decoded).replace(/^([/\\])+/, "");
  const candidate = resolve(root, normalized);
  return candidate === root || candidate.startsWith(`${root}${sep}`) ? candidate : null;
}

function filteredHeaders(headers, host) {
  const result = {};
  for (const [name, value] of Object.entries(headers)) {
    if (["connection", "host", "origin", "referer", "upgrade"].includes(name.toLowerCase())) continue;
    if (value !== undefined) result[name] = value;
  }
  result.host = host;
  return result;
}

function serveFile(path, response, statusCode = 200) {
  response.writeHead(statusCode, {
    "content-type": MIME[extname(path).toLowerCase()] || "application/octet-stream",
    "cache-control": extname(path) === ".html" ? "no-store" : "public, max-age=31536000, immutable",
  });
  createReadStream(path).on("error", () => response.end()).pipe(response);
}

function serveFrontendIndex(path, response) {
  let source;
  try {
    source = readFileSync(path, "utf8");
  } catch {
    response.writeHead(503, { "content-type": "text/plain; charset=utf-8" });
    response.end("Search renderer unavailable");
    return;
  }
  const bridge = '<script src="/__search_desktop__/desktop-route-bridge.js"></script>';
  if (!source.includes(bridge)) {
    source = source.includes("<head>")
      ? source.replace("<head>", `<head>${bridge}`)
      : `${bridge}${source}`;
  }
  response.writeHead(200, {
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store",
  });
  response.end(source);
}

function setSecurityHeaders(response, backendOrigin) {
  response.setHeader("x-content-type-options", "nosniff");
  response.setHeader("referrer-policy", "no-referrer");
  response.setHeader("cross-origin-resource-policy", "same-origin");
  response.setHeader("content-security-policy", `default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' ${backendOrigin}; font-src 'self' data:; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'self'`);
}
