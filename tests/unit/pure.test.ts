import { describe, expect, it } from "vitest";
import { parseRange } from "@/lib/http-range";
import { safeNextPath } from "@/lib/utils";
import { detectImage, sanitizeDisplayName } from "@/modules/assets/image";
import { detectVideo } from "@/modules/generations/video-file";
import { briefSchema } from "@/modules/projects/schema";
import { composePrompt, checkCompatibility, type VideoModelProfile } from "@/modules/video-providers/types";

describe("parseRange", () => {
  it("returns null when there is no usable range", () => {
    expect(parseRange(null, 1000)).toBeNull();
    expect(parseRange("", 1000)).toBeNull();
    expect(parseRange("items=0-5", 1000)).toBeNull();
    expect(parseRange("bytes=-", 1000)).toBeNull();
    expect(parseRange("bytes=0-5,10-20", 1000)).toBeNull(); // multi-range -> full body
  });

  it("parses closed, open-ended and suffix ranges", () => {
    expect(parseRange("bytes=0-99", 1000)).toEqual({ start: 0, end: 99 });
    expect(parseRange("bytes=500-", 1000)).toEqual({ start: 500, end: 999 });
    expect(parseRange("bytes=-100", 1000)).toEqual({ start: 900, end: 999 });
  });

  it("clamps the end and oversized suffixes", () => {
    expect(parseRange("bytes=900-5000", 1000)).toEqual({ start: 900, end: 999 });
    expect(parseRange("bytes=-5000", 1000)).toEqual({ start: 0, end: 999 });
  });

  it("flags unsatisfiable ranges", () => {
    expect(parseRange("bytes=1000-", 1000)).toBe("unsatisfiable");
    expect(parseRange("bytes=2000-3000", 1000)).toBe("unsatisfiable");
    expect(parseRange("bytes=50-10", 1000)).toBe("unsatisfiable");
    expect(parseRange("bytes=-0", 1000)).toBe("unsatisfiable");
  });
});

describe("safeNextPath (open-redirect guard)", () => {
  it("keeps same-site paths", () => {
    expect(safeNextPath("/projects/abc")).toBe("/projects/abc");
    expect(safeNextPath(["/dashboard", "/x"])).toBe("/dashboard");
  });
  it("rejects anything that could leave the site", () => {
    for (const evil of ["//evil.com", "https://evil.com", "/\\evil.com", "javascript:alert(1)", "evil.com", "/ok://x", ""]) {
      expect(safeNextPath(evil)).toBe("/dashboard");
    }
    expect(safeNextPath(undefined)).toBe("/dashboard");
  });
});

describe("detectImage (magic bytes)", () => {
  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);
  const jpg = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 0, 0, 0]);
  const webp = Buffer.concat([Buffer.from("RIFF"), Buffer.from([0, 0, 0, 0]), Buffer.from("WEBP")]);

  it("recognises PNG, JPEG and WebP", () => {
    expect(detectImage(png)).toEqual({ mime: "image/png", ext: "png" });
    expect(detectImage(jpg)).toEqual({ mime: "image/jpeg", ext: "jpg" });
    expect(detectImage(webp)).toEqual({ mime: "image/webp", ext: "webp" });
  });

  it("rejects SVG, HTML, GIF, RIFF-but-not-WebP, empty and truncated input", () => {
    expect(detectImage(Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'))).toBeNull();
    expect(detectImage(Buffer.from("<html></html>"))).toBeNull();
    expect(detectImage(Buffer.from("GIF89a"))).toBeNull();
    expect(detectImage(Buffer.concat([Buffer.from("RIFF"), Buffer.from([0, 0, 0, 0]), Buffer.from("WAVE")]))).toBeNull();
    expect(detectImage(Buffer.alloc(0))).toBeNull();
    expect(detectImage(png.subarray(0, 4))).toBeNull();
  });

  it("sanitises display names (never used for paths)", () => {
    expect(sanitizeDisplayName("../../etc/passwd")).toBe("passwd");
    expect(sanitizeDisplayName("C:\\Users\\me\\photo.png")).toBe("photo.png");
    expect(sanitizeDisplayName("a\u0000b\nc.png")).toBe("abc.png");
    expect(sanitizeDisplayName("   ")).toBe("image");
    expect(sanitizeDisplayName("x".repeat(500))).toHaveLength(120);
  });
});

describe("detectVideo", () => {
  it("accepts mp4/quicktime/webm containers", () => {
    const mp4 = Buffer.alloc(16);
    mp4.write("ftypisom", 4, "ascii");
    expect(detectVideo(mp4)).toEqual({ mime: "video/mp4" });
    const mov = Buffer.alloc(16);
    mov.write("ftypqt  ", 4, "ascii");
    expect(detectVideo(mov)).toEqual({ mime: "video/quicktime" });
    expect(detectVideo(Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 0, 0, 0, 0, 0, 0, 0, 0]))).toEqual({ mime: "video/webm" });
  });

  it("rejects html, json, empty and short input", () => {
    expect(detectVideo(Buffer.from("<!DOCTYPE html><html>"))).toBeNull();
    expect(detectVideo(Buffer.from('{"error":"expired"}   '))).toBeNull();
    expect(detectVideo(Buffer.alloc(0))).toBeNull();
    expect(detectVideo(Buffer.alloc(8))).toBeNull();
  });
});

describe("briefSchema", () => {
  const valid = {
    productName: "Gourde Nomad",
    description: "Gourde isotherme en inox, garde le froid 24h.",
    benefits: ["Garde le froid 24h"],
    targetAudience: "Sportifs urbains",
    tone: "energetic",
    visualStyle: "studio",
    aspectRatio: "9:16",
    durationSeconds: 8,
    language: "fr",
    callToAction: "",
    creativeNotes: "",
  };

  it("accepts a valid brief and turns empty optionals into undefined", () => {
    const parsed = briefSchema.parse(valid);
    expect(parsed.callToAction).toBeUndefined();
    expect(parsed.creativeNotes).toBeUndefined();
  });

  it("coerces a numeric string duration (form posts) but rejects unsupported values", () => {
    expect(briefSchema.parse({ ...valid, durationSeconds: "10" }).durationSeconds).toBe(10);
    expect(briefSchema.safeParse({ ...valid, durationSeconds: 7 }).success).toBe(false);
    expect(briefSchema.safeParse({ ...valid, durationSeconds: "abc" }).success).toBe(false);
  });

  it("rejects unknown presets, bad formats, empty/too many benefits and oversize text", () => {
    expect(briefSchema.safeParse({ ...valid, tone: "angry" }).success).toBe(false);
    expect(briefSchema.safeParse({ ...valid, visualStyle: "x" }).success).toBe(false);
    expect(briefSchema.safeParse({ ...valid, aspectRatio: "4:3" }).success).toBe(false);
    expect(briefSchema.safeParse({ ...valid, language: "xx" }).success).toBe(false);
    expect(briefSchema.safeParse({ ...valid, benefits: [] }).success).toBe(false);
    expect(briefSchema.safeParse({ ...valid, benefits: Array(7).fill("avantage") }).success).toBe(false);
    expect(briefSchema.safeParse({ ...valid, description: "x".repeat(1201) }).success).toBe(false);
    expect(briefSchema.safeParse({ ...valid, productName: " " }).success).toBe(false);
  });

  it("trims whitespace", () => {
    expect(briefSchema.parse({ ...valid, productName: "  Gourde  " }).productName).toBe("Gourde");
  });
});

describe("model capability helpers", () => {
  const profile = (over: Partial<VideoModelProfile["capabilities"]> = {}): VideoModelProfile => ({
    id: "x:y",
    providerId: "fal",
    label: "Model Y",
    description: "",
    capabilities: { durations: [5, 10], aspectRatios: ["16:9"], imageToVideo: false, supportsNegativePrompt: false, ...over },
  });

  it("reports every unsupported dimension", () => {
    const problems = checkCompatibility(profile(), { aspectRatio: "9:16", durationSeconds: 8, withImage: true });
    expect(problems).toHaveLength(3);
    expect(checkCompatibility(profile(), { aspectRatio: "16:9", durationSeconds: 5, withImage: false })).toEqual([]);
  });

  it("folds the negative prompt into the prompt only when the model has no dedicated field", () => {
    expect(composePrompt(profile(), "A bottle", "blurry")).toEqual({ prompt: "A bottle\n\nAvoid: blurry" });
    expect(composePrompt(profile({ supportsNegativePrompt: true }), "A bottle", "blurry")).toEqual({
      prompt: "A bottle",
      negativePrompt: "blurry",
    });
    expect(composePrompt(profile(), "A bottle", "  ")).toEqual({ prompt: "A bottle" });
    expect(composePrompt(profile(), "A bottle", null)).toEqual({ prompt: "A bottle" });
  });
});

describe("error guards survive duplicated module instances", () => {
  // Next.js bundles server modules separately for pages and route handlers, so
  // an error thrown by code from one bundle is NOT `instanceof` the class seen by
  // another. Regression test for a bug caught in a real browser run: a provider
  // error was reported as a generic failure because `instanceof` returned false.
  it("recognises look-alike errors from another copy of the module", async () => {
    const { isAppError } = await import("@/lib/errors");
    const { isProviderApiError } = await import("@/modules/video-providers/http");
    const { isStorageLimitError } = await import("@/modules/storage/types");

    class OtherCopyOfAppError extends Error { readonly __brand = "AppError" as const; code = "NOT_FOUND"; }
    class OtherCopyOfProviderApiError extends Error { readonly __brand = "ProviderApiError" as const; retryable = false; status = 0; provider = "x"; }
    class OtherCopyOfStorageLimitError extends Error { readonly __brand = "StorageLimitError" as const; }

    expect(isAppError(new OtherCopyOfAppError("x"))).toBe(true);
    expect(isProviderApiError(new OtherCopyOfProviderApiError("x"))).toBe(true);
    expect(isStorageLimitError(new OtherCopyOfStorageLimitError("x"))).toBe(true);

    // ...and does not over-match.
    expect(isAppError(new Error("x"))).toBe(false);
    expect(isAppError(null)).toBe(false);
    expect(isAppError({ __brand: "ProviderApiError" })).toBe(false);
    expect(isProviderApiError(new OtherCopyOfAppError("x"))).toBe(false);
    expect(isStorageLimitError("StorageLimitError")).toBe(false);
  });
});
