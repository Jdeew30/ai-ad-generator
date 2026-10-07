import { afterEach, describe, expect, it } from "vitest";
import { createFalProvider, falAppPath } from "@/modules/video-providers/fal";
import { ProviderApiError } from "@/modules/video-providers/http";
import { startStubServer, type StubServer } from "../helpers/stub-server";

let server: StubServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
});

async function setup(handler: Parameters<typeof startStubServer>[0]) {
  server = await startStubServer(handler);
  const provider = createFalProvider({ apiKey: "fal-test", queueUrl: server.url, urlPolicy: "any" });
  return { provider, model: provider.models[0]!, server };
}

const ENDPOINT = "fal-ai/kling-video/v2.1/master/text-to-video";
const job = { externalId: "req_9", providerData: { endpoint: ENDPOINT }, modelId: "fal:fal-ai/kling-video/v2.1/master" };

describe("falAppPath", () => {
  it("keeps only owner/alias, as the official SDK does for status/result/cancel", () => {
    expect(falAppPath("fal-ai/kling-video/v2.1/master/text-to-video")).toBe("fal-ai/kling-video");
    expect(falAppPath("fal-ai/veo3")).toBe("fal-ai/veo3");
    expect(falAppPath("workflows/acme/flow/extra")).toBe("workflows/acme/flow");
  });
});

describe("fal adapter", () => {
  it("submits to the text endpoint with `Key` auth and persists the endpoint", async () => {
    const { provider, model, server } = await setup(() => ({ json: { request_id: "req_9", status: "IN_QUEUE" } }));
    const result = await provider.submit({
      model,
      prompt: "A bottle",
      negativePrompt: "blurry",
      aspectRatio: "16:9",
      durationSeconds: 10,
    });
    expect(result).toEqual({ externalId: "req_9", providerData: { endpoint: ENDPOINT } });

    const req = server.requests[0]!;
    expect(req.method).toBe("POST");
    expect(req.path).toBe(`/${ENDPOINT}`);
    expect(req.headers.authorization).toBe("Key fal-test");
    expect(req.json()).toEqual({ prompt: "A bottle", duration: "10", aspect_ratio: "16:9", negative_prompt: "blurry" });
  });

  it("switches to the image-to-video endpoint when a reference image is given", async () => {
    const { provider, model, server } = await setup(() => ({ json: { request_id: "req_10" } }));
    const result = await provider.submit({
      model,
      prompt: "p",
      aspectRatio: "9:16",
      durationSeconds: 5,
      referenceImage: { bytes: Buffer.from([9, 9]), mimeType: "image/jpeg" },
    });
    expect(result.providerData).toEqual({ endpoint: "fal-ai/kling-video/v2.1/master/image-to-video" });
    const body = server.requests[0]!.json() as Record<string, unknown>;
    expect(body.image_url).toBe(`data:image/jpeg;base64,${Buffer.from([9, 9]).toString("base64")}`);
    expect(body.aspect_ratio).toBeUndefined();
  });

  it("polls the app-level status path (not the full submit path)", async () => {
    const { provider, server } = await setup(() => ({ json: { status: "IN_QUEUE" } }));
    expect(await provider.poll(job)).toEqual({ state: "queued" });
    expect(server.requests[0]!.path).toBe("/fal-ai/kling-video/requests/req_9/status");
  });

  it("maps IN_PROGRESS", async () => {
    const { provider } = await setup(() => ({ json: { status: "IN_PROGRESS" } }));
    expect(await provider.poll(job)).toEqual({ state: "processing" });
  });

  it("on COMPLETED fetches the result and only then reports success", async () => {
    const { provider, server } = await setup((req) =>
      req.path.endsWith("/status")
        ? { json: { status: "COMPLETED" } }
        : { json: { video: { url: "https://v3.fal.media/files/out.mp4" } } },
    );
    expect(await provider.poll(job)).toEqual({
      state: "succeeded",
      output: { videoUrl: "https://v3.fal.media/files/out.mp4" },
    });
    expect(server.requests.map((r) => r.path)).toEqual([
      "/fal-ai/kling-video/requests/req_9/status",
      "/fal-ai/kling-video/requests/req_9",
    ]);
  });

  it("COMPLETED with a 4xx result (e.g. content policy) is a failure with the provider's message", async () => {
    const { provider } = await setup((req) =>
      req.path.endsWith("/status")
        ? { json: { status: "COMPLETED" } }
        : { status: 422, json: { detail: [{ type: "content_policy_violation", msg: "Prompt rejected by safety filter", loc: ["body"] }] } },
    );
    expect(await provider.poll(job)).toEqual({ state: "failed", message: "Prompt rejected by safety filter" });
  });

  it("COMPLETED with a 5xx result is transient, never a success or a terminal failure", async () => {
    const { provider } = await setup((req) =>
      req.path.endsWith("/status") ? { json: { status: "COMPLETED" } } : { status: 503, body: "unavailable" },
    );
    const err = await provider.poll(job).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ProviderApiError);
    expect((err as ProviderApiError).retryable).toBe(true);
  });

  it("COMPLETED without a video url is a failure", async () => {
    const { provider } = await setup((req) =>
      req.path.endsWith("/status") ? { json: { status: "COMPLETED" } } : { json: { something: "else" } },
    );
    expect((await provider.poll(job)).state).toBe("failed");
  });

  it("cancels with PUT on the app-level cancel path", async () => {
    const { provider, server } = await setup(() => ({ json: {} }));
    await provider.cancel!(job);
    expect(server.requests[0]).toMatchObject({ method: "PUT", path: "/fal-ai/kling-video/requests/req_9/cancel" });
  });
});
