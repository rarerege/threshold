// Minimal MCP client over Streamable HTTP (spec 2025-11-25), written against the wire protocol:
// POST JSON-RPC to one endpoint; responses arrive as JSON or as an SSE stream; the server's
// Mcp-Session-Id header is echoed on every later request together with MCP-Protocol-Version.

export const PROTOCOL_VERSION = "2025-11-25";

export class McpError extends Error {
  constructor(message, { code, data, httpStatus } = {}) {
    super(message);
    this.code = code;
    this.data = data;
    this.httpStatus = httpStatus;
  }
}

export function parseSse(text) {
  const messages = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    const data = block.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).replace(/^ /, ""));
    if (data.length) messages.push(JSON.parse(data.join("\n")));
  }
  return messages;
}

export class McpClient {
  /** @param {string} url  @param {(entry: object) => void} onTraffic */
  constructor(url, onTraffic = () => {}) {
    this.url = url;
    this.onTraffic = onTraffic;
    this.nextId = 1;
    this.sessionId = null;
    this.protocolVersion = null;
    this.serverInfo = null;
    this.tools = [];
  }

  async connect() {
    this.sessionId = null;
    this.protocolVersion = null;
    const init = await this.request("initialize", {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: { name: "threshold-web", title: "Threshold (Alexa+ simulator)", version: "0.1.0" },
    });
    this.protocolVersion = init.protocolVersion;
    this.serverInfo = init.serverInfo;
    await this.notify("notifications/initialized");
    this.tools = (await this.request("tools/list")).tools;
    return init;
  }

  async callTool(name, args = {}, { quiet = false } = {}) {
    try {
      return await this.request("tools/call", { name, arguments: args }, { quiet });
    } catch (e) {
      if (e.httpStatus === 404 && this.sessionId) {
        // Session expired on the server (idle timeout or restart): start a new one and retry once.
        await this.connect();
        return this.request("tools/call", { name, arguments: args }, { quiet });
      }
      throw e;
    }
  }

  async request(method, params, opts) {
    const id = this.nextId++;
    const msg = { jsonrpc: "2.0", id, method, ...(params !== undefined && { params }) };
    const replies = await this.#post(msg, opts);
    const reply = replies.find((m) => m.id === id);
    if (!reply) throw new McpError(`No response to ${method}`);
    if (reply.error) throw new McpError(reply.error.message, reply.error);
    return reply.result;
  }

  async notify(method, params) {
    await this.#post({ jsonrpc: "2.0", method, ...(params !== undefined && { params }) });
  }

  async #post(msg, { quiet = false } = {}) {
    const headers = { "Content-Type": "application/json", Accept: "application/json, text/event-stream" };
    if (this.sessionId) headers["Mcp-Session-Id"] = this.sessionId;
    if (this.protocolVersion) headers["MCP-Protocol-Version"] = this.protocolVersion;
    const started = performance.now();
    const res = await fetch(this.url, { method: "POST", headers, body: JSON.stringify(msg) });
    const sid = res.headers.get("Mcp-Session-Id");
    if (sid && !this.sessionId) this.sessionId = sid;
    const type = res.headers.get("Content-Type") || "";
    const body = res.status === 202 ? "" : await res.text();
    let replies = [];
    if (type.includes("text/event-stream")) replies = parseSse(body);
    else if (body && type.includes("application/json")) replies = [].concat(JSON.parse(body));
    this.onTraffic({ request: msg, status: res.status, replies, ms: Math.round(performance.now() - started),
                     sessionId: this.sessionId, quiet });
    if (!res.ok) {
      const err = replies.find((m) => m.error)?.error;
      throw new McpError(err?.message || `HTTP ${res.status}`, { ...err, httpStatus: res.status });
    }
    return replies;
  }
}
