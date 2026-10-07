import http from "node:http";
import type { AddressInfo } from "node:net";

/**
 * TEST DOUBLE — never imported by src/. A tiny local HTTP server used to
 * exercise the *real* provider adapters (real fetch, real URLs/headers/bodies)
 * without network access or API keys. It proves our client logic; it does not
 * prove the live providers behave this way (see README "État réel").
 */

export interface RecordedRequest {
  method: string;
  path: string;
  headers: http.IncomingHttpHeaders;
  body: Buffer;
  json(): unknown;
}

export interface StubResponse {
  status?: number;
  json?: unknown;
  body?: Buffer | string;
  headers?: Record<string, string>;
}

export type StubHandler = (req: RecordedRequest) => StubResponse | Promise<StubResponse>;

export interface StubServer {
  url: string;
  requests: RecordedRequest[];
  setHandler(handler: StubHandler): void;
  close(): Promise<void>;
}

export async function startStubServer(initial: StubHandler): Promise<StubServer> {
  let handler = initial;
  const requests: RecordedRequest[] = [];

  const server = http.createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    const body = Buffer.concat(chunks);
    const recorded: RecordedRequest = {
      method: req.method ?? "GET",
      path: req.url ?? "/",
      headers: req.headers,
      body,
      json: () => JSON.parse(body.toString("utf8")),
    };
    requests.push(recorded);

    try {
      const out = await handler(recorded);
      const headers: Record<string, string> = { ...(out.headers ?? {}) };
      let payload: Buffer | string;
      if (out.json !== undefined) {
        headers["content-type"] ??= "application/json";
        payload = JSON.stringify(out.json);
      } else {
        payload = out.body ?? "";
      }
      res.writeHead(out.status ?? 200, headers);
      res.end(payload);
    } catch (error) {
      res.writeHead(500, { "content-type": "text/plain" });
      res.end(String(error));
    }
  });

  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;

  return {
    url: `http://127.0.0.1:${port}`,
    requests,
    setHandler: (h) => {
      handler = h;
    },
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  };
}

/** Smallest thing our verifier accepts as an MP4: an ISO-BMFF `ftyp` box + padding. Not playable. */
export function fakeMp4(size = 4096): Buffer {
  const b = Buffer.alloc(size, 0x20);
  b.writeUInt32BE(24, 0);
  b.write("ftypisom", 4, "ascii");
  return b;
}
