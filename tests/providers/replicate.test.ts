import { afterEach, describe, expect, it } from "vitest";
import { createReplicateProvider, extractReplicateVideoUrl } from "@/modules/video-providers/replicate";
import { fakeMp4, startStubServer, type StubServer } from "../helpers/stub-server";

let server: StubServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
});

async function setup(handler: Parameters<typeof startStubServer>[0]) {
  server = await startStubServer(handler);
  const provider = createReplicateProvider({ apiToken: "r8_test", baseUrl: server.url, urlPolicy: "any" });
  return { provider, model: provider.models[0]!, server };
}

const job = { externalId: "pred_1", providerData: null, modelId: "replicate:bytedance/seedance-1-lite" };

describe("Replicate adapter", () => {
  it("is not configured without a token", () => {
    expect(createReplicateProvider({}).isConfigured()).toBe(false);
  });

  it("creates a prediction on the official model endpoint with bearer auth", async () => {
    const { provider, model, server } = await setup(() => ({ status: 201, json: { id: "pred_1", status: "starting" } }));
    const result = await provider.submit({ model, prompt: "A bottle", aspectRatio: "9:16", durationSeconds: 5 });
    expect(result.externalId).toBe("pred_1");

    const req = server.requests[0]!;
    expect(req.method).toBe("POST");
    expect(req.path).toBe("/models/bytedance/seedance-1-lite/predictions");
    expect(req.headers.authorization).toBe("Bearer r8_test");
    expect(req.json()).toEqual({ input: { prompt: "A bottle", duration: 5, aspect_ratio: "9:16" } });
  });

  it("sends the reference image as a data URI when provided", async () => {
    const { provider, model, server } = await setup(() => ({ status: 201, json: { id: "pred_1", status: "starting" } }));
    await provider.submit({
      model,
      prompt: "p",
      aspectRatio: "1:1",
      durationSeconds: 10,
      referenceImage: { bytes: Buffer.from([1, 2, 3]), mimeType: "image/jpeg" },
    });
    const body = server.requests[0]!.json() as { input: { image: string } };
    expect(body.input.image).toBe(`data:image/jpeg;base64,${Buffer.from([1, 2, 3]).toString("base64")}`);
  });

  it("surfaces 422 validation details", async () => {
    const { provider, model } = await setup(() => ({
      status: 422,
      json: { title: "Input validation failed", detail: "duration: must be one of 5, 10", status: 422 },
    }));
    await expect(provider.submit({ model, prompt: "p", aspectRatio: "9:16", durationSeconds: 5 })).rejects.toThrow(
      "duration: must be one of 5, 10",
    );
  });

  it("maps every documented prediction status", async () => {
    let body: unknown;
    const { provider } = await setup(() => ({ json: body }));

    body = { id: "pred_1", status: "starting" };
    expect(await provider.poll(job)).toEqual({ state: "queued" });
    body = { id: "pred_1", status: "processing" };
    expect(await provider.poll(job)).toEqual({ state: "processing" });
    body = { id: "pred_1", status: "succeeded", output: "https://replicate.delivery/x/out.mp4" };
    expect(await provider.poll(job)).toEqual({ state: "succeeded", output: { videoUrl: "https://replicate.delivery/x/out.mp4" } });
    body = { id: "pred_1", status: "failed", error: "NSFW content detected" };
    expect(await provider.poll(job)).toEqual({ state: "failed", message: "NSFW content detected" });
    body = { id: "pred_1", status: "canceled" };
    expect(await provider.poll(job)).toEqual({ state: "canceled" });
    body = { id: "pred_1", status: "aborted" };
    expect(await provider.poll(job)).toEqual({ state: "canceled" });
  });

  it("does not report success when a succeeded prediction has no usable output", async () => {
    const { provider } = await setup(() => ({ json: { id: "pred_1", status: "succeeded", output: null } }));
    const status = await provider.poll(job);
    expect(status.state).toBe("failed");
  });

  it("downloads from the delivery URL without sending credentials", async () => {
    const mp4 = fakeMp4();
    const { provider, server } = await setup(() => ({ body: mp4, headers: { "content-type": "video/mp4" } }));
    const res = await provider.download(job, { state: "succeeded", output: { videoUrl: `${server.url}/out.mp4` } });
    expect(Buffer.from(await res.arrayBuffer()).equals(mp4)).toBe(true);
    expect(server.requests[0]!.headers.authorization).toBeUndefined();
  });

  it("refuses to download from a non-HTTPS / internal URL under the production policy", async () => {
    const provider = createReplicateProvider({ apiToken: "t" }); // default policy
    await expect(
      provider.download(job, { state: "succeeded", output: { videoUrl: "http://169.254.169.254/latest/meta-data" } }),
    ).rejects.toThrow(/HTTPS/);
    await expect(
      provider.download(job, { state: "succeeded", output: { videoUrl: "https://localhost/x.mp4" } }),
    ).rejects.toThrow(/interne/);
  });

  it("cancels via POST /predictions/{id}/cancel", async () => {
    const { provider, server } = await setup(() => ({ json: { id: "pred_1", status: "canceled" } }));
    await provider.cancel!(job);
    expect(server.requests[0]).toMatchObject({ method: "POST", path: "/predictions/pred_1/cancel" });
  });
});

describe("extractReplicateVideoUrl", () => {
  it("handles string, array and object outputs", () => {
    expect(extractReplicateVideoUrl("https://a/b.mp4")).toBe("https://a/b.mp4");
    expect(extractReplicateVideoUrl(["https://a/b.mp4"])).toBe("https://a/b.mp4");
    expect(extractReplicateVideoUrl({ video: "https://a/b.mp4" })).toBe("https://a/b.mp4");
    expect(extractReplicateVideoUrl(null)).toBeUndefined();
    expect(extractReplicateVideoUrl({})).toBeUndefined();
  });
});
