import { afterEach, describe, expect, it } from "vitest";
import { ProviderApiError, assertSafeRemoteUrl, extractErrorMessage, fetchFollowingRedirects } from "@/modules/video-providers/http";
import { startStubServer, type StubServer } from "../helpers/stub-server";

describe("assertSafeRemoteUrl (production policy)", () => {
  it("accepts public https URLs", () => {
    expect(assertSafeRemoteUrl("https://replicate.delivery/x/out.mp4").hostname).toBe("replicate.delivery");
    expect(assertSafeRemoteUrl("https://v3.fal.media/files/a.mp4").protocol).toBe("https:");
    expect(assertSafeRemoteUrl("https://8.8.8.8/x.mp4").hostname).toBe("8.8.8.8");
  });

  it("rejects non-https, loopback, private, link-local and metadata addresses", () => {
    const bad = [
      "http://replicate.delivery/x.mp4",
      "ftp://x.com/a",
      "file:///etc/passwd",
      "https://localhost/x",
      "https://app.localhost/x",
      "https://127.0.0.1/x",
      "https://127.1.2.3/x",
      "https://10.0.0.5/x",
      "https://172.16.0.1/x",
      "https://172.31.255.255/x",
      "https://192.168.1.1/x",
      "https://169.254.169.254/latest/meta-data",
      "https://100.64.0.1/x",
      "https://0.0.0.0/x",
      "https://[::1]/x",
      "https://[fd00::1]/x",
      "https://[fe80::1]/x",
      "https://[::ffff:10.0.0.1]/x",
      "https://[::ffff:127.0.0.1]/x",
      "https://[::ffff:169.254.169.254]/x",
      "https://[::ffff:a9fe:a9fe]/x",
      "https://[64:ff9b::a00:1]/x",
      "https://[64:ff9b::7f00:1]/x",
      "https://[2002:a00:1::1]/x",
      "https://[2002:7f00:1::]/x",
      "https://[::127.0.0.1]/x",
      "https://[fec0::1]/x",
      "https://[0:0:0:0:0:0:0:1]/x",
      "https://db.internal/x",
      "https://printer.local/x",
    ];
    for (const url of bad) expect(() => assertSafeRemoteUrl(url), url).toThrow(ProviderApiError);
  });

  it("does not over-block neighbours of private ranges", () => {
    expect(() => assertSafeRemoteUrl("https://172.32.0.1/x")).not.toThrow();
    expect(() => assertSafeRemoteUrl("https://172.15.0.1/x")).not.toThrow();
    expect(() => assertSafeRemoteUrl("https://100.128.0.1/x")).not.toThrow();
    // Public IPv6 and public IPv4 embedded in IPv6 forms must still work.
    expect(() => assertSafeRemoteUrl("https://[2606:4700:4700::1111]/x")).not.toThrow();
    expect(() => assertSafeRemoteUrl("https://[::ffff:8.8.8.8]/x")).not.toThrow();
    expect(() => assertSafeRemoteUrl("https://[64:ff9b::808:808]/x")).not.toThrow();
  });

  it("rejects garbage", () => {
    expect(() => assertSafeRemoteUrl("not a url")).toThrow(ProviderApiError);
  });
});

describe("fetchFollowingRedirects", () => {
  let a: StubServer | undefined;
  let b: StubServer | undefined;
  afterEach(async () => {
    await a?.close();
    await b?.close();
    a = b = undefined;
  });

  it("follows redirects but drops credentials when the origin changes", async () => {
    b = await startStubServer(() => ({ body: "final", headers: { "content-type": "video/mp4" } }));
    a = await startStubServer(() => ({ status: 302, headers: { location: `${b!.url}/file.mp4` } }));
    const res = await fetchFollowingRedirects(
      `${a.url}/start`,
      { headers: { Authorization: "Bearer secret" } },
      { policy: "any", provider: "t" },
    );
    expect(await res.text()).toBe("final");
    expect(a.requests[0]!.headers.authorization).toBe("Bearer secret"); // sent to the original host
    expect(b.requests[0]!.headers.authorization).toBeUndefined(); // never to another origin
  });

  it("keeps credentials on same-origin redirects", async () => {
    a = await startStubServer((req) =>
      req.path === "/start" ? { status: 302, headers: { location: "/final" } } : { body: "ok" },
    );
    await fetchFollowingRedirects(`${a.url}/start`, { headers: { Authorization: "Bearer k" } }, { policy: "any", provider: "t" });
    expect(a.requests[1]!.headers.authorization).toBe("Bearer k");
  });

  it("blocks a redirect to an internal address under the production policy", async () => {
    // The first hop must be public https for the policy to even start; emulate with a fake fetch.
    const fakeFetch = (async (url: URL | string) => {
      const u = String(url);
      if (u.startsWith("https://cdn.example.com/")) {
        return new Response(null, { status: 302, headers: { location: "http://169.254.169.254/latest/meta-data/" } });
      }
      throw new Error("must not be fetched: " + u);
    }) as typeof fetch;
    await expect(
      fetchFollowingRedirects("https://cdn.example.com/a.mp4", {}, { fetch: fakeFetch, provider: "t" }),
    ).rejects.toThrow(/HTTPS|interne/);
  });

  it("stops after too many redirects", async () => {
    a = await startStubServer(() => ({ status: 302, headers: { location: "/again" } }));
    await expect(fetchFollowingRedirects(`${a.url}/x`, {}, { policy: "any", provider: "t", maxRedirects: 2 })).rejects.toThrow(/redirections/);
  });

  it("classifies HTTP failures as retryable (429/5xx) or permanent (4xx)", async () => {
    a = await startStubServer((req) => (req.path === "/gone" ? { status: 404, body: "x" } : { status: 503, body: "x" }));
    const gone = await fetchFollowingRedirects(`${a.url}/gone`, {}, { policy: "any", provider: "t" }).catch((e: unknown) => e);
    expect((gone as ProviderApiError).retryable).toBe(false);
    const busy = await fetchFollowingRedirects(`${a.url}/busy`, {}, { policy: "any", provider: "t" }).catch((e: unknown) => e);
    expect((busy as ProviderApiError).retryable).toBe(true);
  });
});

describe("extractErrorMessage", () => {
  it("understands each provider's error shape", () => {
    expect(extractErrorMessage({ error: { message: "OpenAI says no" } }, "x")).toBe("OpenAI says no");
    expect(extractErrorMessage({ detail: "Replicate says no" }, "x")).toBe("Replicate says no");
    expect(extractErrorMessage({ detail: [{ msg: "a" }, { msg: "b" }] }, "x")).toBe("a; b");
    expect(extractErrorMessage({ message: "plain" }, "x")).toBe("plain");
    expect(extractErrorMessage({ title: "Title only" }, "x")).toBe("Title only");
    expect(extractErrorMessage("raw text", "x")).toBe("raw text");
    expect(extractErrorMessage({}, "fallback")).toBe("fallback");
    expect(extractErrorMessage(null, "fallback")).toBe("fallback");
  });

  it("caps very long messages", () => {
    expect(extractErrorMessage({ detail: "z".repeat(5000) }, "x").length).toBeLessThanOrEqual(500);
  });
});
