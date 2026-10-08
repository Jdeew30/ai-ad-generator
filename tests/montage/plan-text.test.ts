import { describe, expect, it } from "vitest";
import { clip, planTimeline, summarizePlan } from "@/modules/montage/plan";
import { loadFont, pathData, sanitizeForFont, typesetCentered, wrapText } from "@/modules/montage/text";
import { FPS, type MontageSpec } from "@/modules/montage/types";

const base = (over: Partial<MontageSpec> = {}): MontageSpec => ({
  aspectRatio: "9:16",
  durationSeconds: 8,
  images: [new Uint8Array(1)],
  headline: "iPhone 18",
  captions: ["Écran lumineux", "Photo nocturne", "Autonomie longue durée", "Quatrième ignorée"],
  callToAction: "Découvrez-le",
  ...over,
});

describe("planTimeline", () => {
  it("always adds up to exactly the requested number of frames, with scenes of at least 2 s", () => {
    for (const durationSeconds of [4, 5, 6, 8, 10, 12]) {
      for (const images of [1, 2, 5]) {
        const plan = planTimeline(base({ durationSeconds, images: Array.from({ length: images }, () => new Uint8Array(1)) }));
        expect(plan.reduce((s, p) => s + p.frames, 0), `${durationSeconds}s/${images} photos`).toBe(durationSeconds * FPS);
        for (const scene of plan) expect(scene.frames / FPS).toBeGreaterThanOrEqual(2);
      }
    }
  });

  it("shows the product name first and the call to action last (end card)", () => {
    const plan = planTimeline(base({ durationSeconds: 10 }));
    expect(plan[0]!.texts[0]).toEqual({ text: "iPhone 18", style: "title" });
    const last = plan.at(-1)!;
    expect(last.isEndCard).toBe(true);
    expect(last.texts[0]).toEqual({ text: "Découvrez-le", style: "cta" });
    expect(plan.filter((p) => p.isEndCard)).toHaveLength(1);
  });

  it("uses at most 3 benefits, in order, one per middle scene", () => {
    const plan = planTimeline(base({ durationSeconds: 12 }));
    const middle = plan.slice(1, -1).flatMap((p) => p.texts.map((t) => t.text));
    expect(middle).toEqual(["Écran lumineux", "Photo nocturne", "Autonomie longue durée"]);
    expect(JSON.stringify(plan)).not.toContain("Quatrième");
  });

  it("with only two scenes, the first benefit goes under the product name", () => {
    const plan = planTimeline(base({ durationSeconds: 5 }));
    expect(plan).toHaveLength(2);
    expect(plan[0]!.texts).toEqual([
      { text: "iPhone 18", style: "title" },
      { text: "Écran lumineux", style: "body" },
    ]);
  });

  it("falls back to the product name on the end card when there is no call to action", () => {
    const last = planTimeline(base({ callToAction: undefined, durationSeconds: 6 })).at(-1)!;
    expect(last.texts).toEqual([{ text: "iPhone 18", style: "cta" }]);
  });

  it("even the shortest montage has an end card (so the logo and call to action always appear)", () => {
    const plan = planTimeline(base({ durationSeconds: 4 }));
    expect(plan).toHaveLength(2);
    expect(plan.at(-1)!.isEndCard).toBe(true);
  });

  it("cycles photos and alternates zoom direction", () => {
    const plan = planTimeline(base({ durationSeconds: 12, images: [new Uint8Array(1), new Uint8Array(2)] }));
    expect(plan.map((p) => p.imageIndex)).toEqual([0, 1, 0, 1, 0]);
    expect(plan.map((p) => p.zoom)).toEqual(["in", "out", "in", "out", "in"]);
  });

  it("clips very long text", () => {
    expect(clip("x".repeat(500)).length).toBe(90);
    expect(clip("  a   b  ")).toBe("a b");
  });
});

describe("text typesetting", () => {
  it("loads the bundled font and measures/wraps within the box", async () => {
    const font = await loadFont("Bold");
    const lines = wrapText(font, "Un très long nom de produit qui doit passer à la ligne", 80, 500, 3);
    expect(lines.length).toBeGreaterThan(1);
    for (const line of lines) expect(font.getAdvanceWidth(line, 80, { kerning: true })).toBeLessThanOrEqual(500 + 1);
  });

  it("splits a single over-wide word instead of overflowing", async () => {
    const font = await loadFont("Bold");
    const lines = wrapText(font, "Anticonstitutionnellement".repeat(2), 80, 400, 5);
    for (const line of lines) expect(font.getAdvanceWidth(line.replace("…", ""), 80)).toBeLessThanOrEqual(401);
  });

  it("ends with an ellipsis when it must truncate", async () => {
    const font = await loadFont("Bold");
    const lines = wrapText(font, "mot ".repeat(60), 80, 300, 2);
    expect(lines).toHaveLength(2);
    expect(lines[1]!.endsWith("…")).toBe(true);
  });

  it("drops characters the font cannot draw instead of rendering boxes", async () => {
    const font = await loadFont("Medium");
    expect(sanitizeForFont(font, "Café 🔥 déjà vu 日本")).toBe("Café déjà vu");
    expect(sanitizeForFont(font, "  a \n\t b ")).toBe("a b");
    expect(sanitizeForFont(font, "🔥🔥")).toBe("");
  });

  it("emits one outline path per line and keeps text out of the markup (no injection)", async () => {
    const font = await loadFont("Bold");
    const block = typesetCentered(font, ["<script>alert(1)</script>", "ligne 2"], { centerX: 500, baselineTop: 100, fontSize: 60, lineHeight: 70, fill: "#fff" });
    expect(block.svg.match(/<path /g)).toHaveLength(2);
    expect(block.svg).not.toContain("<script");
    expect(block.svg).not.toContain("alert");
  });
});

describe("summarizePlan (what the UI and the stored description promise)", () => {
  const input = { photoCount: 5, benefitCount: 3, hasLogo: true, hasCallToAction: true };

  it("never promises more than the plan renders, for every duration", () => {
    for (const durationSeconds of [4, 5, 6, 8, 10, 12]) {
      const summary = summarizePlan({ ...input, durationSeconds });
      const plan = planTimeline({
        aspectRatio: "9:16",
        durationSeconds,
        images: Array.from({ length: 5 }, () => new Uint8Array(1)),
        headline: "H",
        captions: ["b1", "b2", "b3"],
        callToAction: "cta",
      });
      const drawn = plan.flatMap((s) => s.texts.map((t) => t.text));
      expect(summary.scenes).toBe(plan.length);
      expect(summary.photosShown).toBe(new Set(plan.map((s) => s.imageIndex)).size);
      expect(summary.benefitsShown).toBe(["b1", "b2", "b3"].filter((b) => drawn.includes(b)).length);
      expect(summary.logoShown).toBe(true);
    }
  });

  it("shows everything from 10 s up, and says so when a duration is too short", () => {
    expect(summarizePlan({ ...input, durationSeconds: 10 })).toMatchObject({ photosShown: 5, benefitsShown: 3 });
    const short = summarizePlan({ ...input, durationSeconds: 5 });
    expect(short.photosShown).toBeLessThan(5);
    expect(short.benefitsShown).toBeLessThan(3);
  });

  it("does not claim a logo or benefits that do not exist", () => {
    expect(summarizePlan({ photoCount: 1, benefitCount: 0, hasLogo: false, hasCallToAction: false, durationSeconds: 8 })).toMatchObject({
      photosShown: 1,
      benefitsShown: 0,
      logoShown: false,
    });
  });
});

describe("pathData (opentype's own serializer can emit NaN)", () => {
  it("never produces NaN for real text, and keeps every glyph", async () => {
    const font = await loadFont("Bold");
    const sentences = ["Découvrez-le maintenant", "iPhone 18", "Garde le froid 24 h", "Écran pensé pour vos yeux", "Photo et vidéo sans compromis", "Œuvre d'été — « à très bientôt »"];
    for (const text of sentences) {
      for (const x of [0, 0.5, 13.37, 627.2, 1234.567, -40.1]) {
        const d = pathData(font.getPath(text, x, 57.12, 80, { kerning: true }).commands);
        expect(d, `${text} @ ${x}`).not.toContain("NaN");
        expect(d.startsWith("M")).toBe(true);
        // one contour (M ... Z) per visible glyph part: at least as many as letters without holes
        expect((d.match(/M/g) ?? []).length).toBeGreaterThanOrEqual(text.replace(/\s/g, "").length * 0.8);
      }
    }
  });

  it("refuses to emit an invalid path instead of silently truncating the line", () => {
    expect(() => pathData([{ type: "M", x: 1, y: Number.NaN }])).toThrow(/non finie/);
    expect(() => pathData([{ type: "L", x: Infinity, y: 0 }])).toThrow();
    expect(() => pathData([{ type: "Q", x1: 1, y1: 1, x: 2 }])).toThrow();
    expect(pathData([{ type: "M", x: 1.234567, y: 2 }, { type: "L", x: 3, y: 4.5 }, { type: "Z" }])).toBe("M1.23 2L3 4.5Z");
  });
});
