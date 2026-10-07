import { afterEach, describe, expect, it } from "vitest";
import { createOpenAiProvider } from "@/modules/video-providers/openai";
import { ProviderApiError } from "@/modules/video-providers/http";
import { fakeMp4, startStubServer, type StubServer } from "../helpers/stub-server";

let server: StubServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
});

async function setup(handler: Parameters<typeof startStubServer>[0]) {
  server = await startStubServer(handler);
  const provider = createOpenAiProvider({ apiKey: "sk-test", baseUrl: server.url, urlPolicy: "any" });
  const model = provider.models.find((m) => m.id === "openai:sora-2")!;
  return { provider, model, server };
}

const job = { externalId: "video_123", providerData: null, modelId: "openai:sora-2" };

describe("OpenAI adapter", () => {
  it("reports not configured without a key and refuses to run", async () => {
    const provider = createOpenAiProvider({});
    expect(provider.isConfigured()).toBe(false);
    await expect(
      provider.submit({ model: provider.models[0]!, prompt: "x", aspectRatio: "9:16", durationSeconds: 4 }),
    ).rejects.toThrow(/OPENAI_API_KEY/);
  });

  it("submits a multipart request with bearer auth, mapped size and seconds", async () => {
    const { provider, model, server } = await setup(() => ({ json: { id: "video_123", status: "queued" } }));
    const result = await provider.submit({ model, prompt: "A bottle on a rock", aspectRatio: "9:16", durationSeconds: 8 });

    expect(result.externalId).toBe("video_123");
    const req = server.requests[0]!;
    expect(req.method).toBe("POST");
    expect(req.path).toBe("/videos");
    expect(req.headers.authorization).toBe("Bearer sk-test");
    expect(req.headers["content-type"]).toMatch(/^multipart\/form-data; boundary=/);
    const raw = req.body.toString("utf8");
    expect(raw).toContain('name="model"\r\n\r\nsora-2');
    expect(raw).toContain('name="seconds"\r\n\r\n8');
    expect(raw).toContain('name="size"\r\n\r\n720x1280');
    expect(raw).toContain("A bottle on a rock");
  });

  it("maps 16:9 to 1280x720", async () => {
    const { provider, model, server } = await setup(() => ({ json: { id: "v", status: "queued" } }));
    await provider.submit({ model, prompt: "p", aspectRatio: "16:9", durationSeconds: 4 });
    expect(server.requests[0]!.body.toString("utf8")).toContain('name="size"\r\n\r\n1280x720');
  });

  it("surfaces API validation errors instead of pretending success", async () => {
    const { provider, model } = await setup(() => ({
      status: 400,
      json: { error: { message: "Invalid seconds", type: "invalid_request_error" } },
    }));
    const err = await provider
      .submit({ model, prompt: "p", aspectRatio: "9:16", durationSeconds: 4 })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ProviderApiError);
    expect((err as ProviderApiError).message).toBe("Invalid seconds");
    expect((err as ProviderApiError).retryable).toBe(false);
  });

  it("never retries a submit (could create a second billed job)", async () => {
    const { provider, model, server } = await setup(() => ({ status: 503, json: { error: { message: "overloaded" } } }));
    await expect(provider.submit({ model, prompt: "p", aspectRatio: "9:16", durationSeconds: 4 })).rejects.toThrow();
    expect(server.requests).toHaveLength(1);
  });

  it("maps poll statuses", async () => {
    const states: Record<string, unknown> = {
      queued: { id: "video_123", status: "queued" },
      in_progress: { id: "video_123", status: "in_progress", progress: 42.4 },
      completed: { id: "video_123", status: "completed", progress: 100 },
      failed: { id: "video_123", status: "failed", error: { code: "moderation_blocked", message: "Blocked by moderation" } },
    };
    let current = "queued";
    const { provider } = await setup(() => ({ json: states[current] }));

    expect(await provider.poll(job)).toEqual({ state: "queued" });
    current = "in_progress";
    expect(await provider.poll(job)).toEqual({ state: "processing", progress: 42 });
    current = "completed";
    expect(await provider.poll(job)).toEqual({ state: "succeeded" });
    current = "failed";
    expect(await provider.poll(job)).toEqual({ state: "failed", message: "Blocked by moderation" });
  });

  it("treats an unknown status as a retryable anomaly, not a result", async () => {
    const { provider } = await setup(() => ({ json: { id: "video_123", status: "weird" } }));
    const err = await provider.poll(job).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ProviderApiError);
    expect((err as ProviderApiError).retryable).toBe(true);
  });

  it("retries transient poll errors (GET only)", async () => {
    let calls = 0;
    const { provider } = await setup(() => {
      calls += 1;
      return calls < 3 ? { status: 502, body: "bad gateway" } : { json: { id: "video_123", status: "completed" } };
    });
    expect(await provider.poll(job)).toEqual({ state: "succeeded" });
    expect(calls).toBe(3);
  });

  it("downloads the authenticated content endpoint", async () => {
    const mp4 = fakeMp4();
    const { provider, server } = await setup(() => ({ body: mp4, headers: { "content-type": "video/mp4" } }));
    const res = await provider.download(job, { state: "succeeded" });
    expect(Buffer.from(await res.arrayBuffer()).equals(mp4)).toBe(true);
    const req = server.requests[0]!;
    expect(req.path).toBe("/videos/video_123/content");
    expect(req.headers.authorization).toBe("Bearer sk-test");
  });

  it("does not offer cancellation (the API has no such endpoint)", () => {
    expect(createOpenAiProvider({ apiKey: "k" }).cancel).toBeUndefined();
  });
});
