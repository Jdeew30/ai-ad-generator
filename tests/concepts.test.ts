import sharp from "sharp";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { getDb } from "@/lib/db";
import { createAnthropicConceptGenerator, DEFAULT_ANTHROPIC_MODEL } from "@/modules/concepts/anthropic";
import { conceptSchema, llmConceptSchema } from "@/modules/concepts/schema";
import { generateConcept, getLatestConcept, updateConceptPrompt } from "@/modules/concepts/service";
import { templateConceptGenerator } from "@/modules/concepts/template";
import { setConceptGeneratorForTests } from "@/modules/concepts";
import type { ConceptBrief } from "@/modules/concepts/types";
import { getStorage } from "@/modules/storage";
import { createProject, createUser, resetDb } from "./helpers/db";
import { startStubServer, type StubServer } from "./helpers/stub-server";

const db = getDb();

const brief: ConceptBrief = {
  productName: "Gourde Nomad",
  description: "Gourde isotherme en inox qui garde le froid 24h et le chaud 12h.",
  benefits: ["Garde le froid 24h", "Sans BPA", "Anti-fuite"],
  targetAudience: "Sportifs urbains de 25 à 40 ans",
  tone: "energetic",
  visualStyle: "studio",
  aspectRatio: "9:16",
  durationSeconds: 8,
  language: "fr",
  callToAction: "Commandez maintenant",
  creativeNotes: null,
};

const validLlmConcept = {
  title: "Fraîcheur nomade",
  hook: "Une goutte d'eau glacée coule sur l'inox",
  summary: "Une gourde qui accompagne chaque aventure urbaine.",
  scenes: [
    { durationSeconds: 4, visual: "Gros plan sur la gourde givrée", camera: "Travelling lent", textOverlay: "24h de froid", voiceover: "Gardez la fraîcheur." },
    { durationSeconds: 4, visual: "La gourde dans un sac à dos", camera: "Plan fixe", textOverlay: "", voiceover: "Partout avec vous." },
  ],
  voiceoverScript: "Gardez la fraîcheur. Partout avec vous.",
  callToAction: "Commandez maintenant",
  musicMood: "Électro léger et rythmé",
  videoPrompt:
    "Studio product shot of a brushed stainless-steel insulated water bottle covered in condensation, slow macro dolly-in, soft diffused lighting, crisp reflections, seamless light-grey backdrop.",
  negativePrompt: "distorted product, flicker, watermark",
};

function messagesResponse(text: string, stopReason = "end_turn") {
  return {
    id: "msg_test",
    type: "message",
    role: "assistant",
    model: DEFAULT_ANTHROPIC_MODEL,
    content: [{ type: "text", text }],
    stop_reason: stopReason,
    stop_sequence: null,
    usage: { input_tokens: 100, output_tokens: 200 },
  };
}

describe("template generator (non-AI)", () => {
  it("is labelled TEMPLATE, has no model, and produces a valid, deterministic payload", async () => {
    const a = await templateConceptGenerator.generate({ brief, images: [] });
    const b = await templateConceptGenerator.generate({ brief, images: [] });
    expect(a).toEqual(b);
    expect(a.source).toBe("TEMPLATE");
    expect(a.model).toBeNull();
    expect(conceptSchema.safeParse(a.payload).success).toBe(true);
    expect(a.payload.videoPrompt).toContain("Gourde Nomad");
    expect(a.payload.videoPrompt).toContain("Garde le froid 24h");
  });

  it("does no creative writing: no hook, no voice-over", async () => {
    const { payload } = await templateConceptGenerator.generate({ brief, images: [] });
    expect(payload.hook).toBe("");
    expect(payload.voiceoverScript).toBe("");
    expect(payload.scenes.every((s) => s.voiceover === "")).toBe(true);
  });

  it("splits the duration across scenes exactly", async () => {
    for (const durationSeconds of [4, 5, 6, 8, 10, 12]) {
      const { payload } = await templateConceptGenerator.generate({ brief: { ...brief, durationSeconds }, images: [] });
      expect(payload.scenes.reduce((s, x) => s + x.durationSeconds, 0)).toBe(durationSeconds);
    }
  });

  it("never exceeds the prompt limit even with huge inputs", async () => {
    const { payload } = await templateConceptGenerator.generate({
      brief: { ...brief, description: "x".repeat(5000), creativeNotes: "y".repeat(5000), targetAudience: "z".repeat(5000) },
      images: [],
    });
    expect(payload.videoPrompt.length).toBeLessThanOrEqual(2000);
  });
});

describe("Anthropic generator (real SDK against a local test double)", () => {
  let server: StubServer;
  afterEach(async () => server?.close());

  async function setup(handler: Parameters<typeof startStubServer>[0], model?: string) {
    server = await startStubServer(handler);
    return createAnthropicConceptGenerator({ apiKey: "sk-ant-test", baseUrl: server.url, model });
  }

  it("sends a structured-output request in the Opus 5.5 shape and returns a validated concept", async () => {
    const generator = await setup(() => ({ json: messagesResponse(JSON.stringify(validLlmConcept)) }));
    const png = await sharp({ create: { width: 32, height: 32, channels: 3, background: "#00f" } }).jpeg().toBuffer();

    const draft = await generator.generate({
      brief,
      images: [{ kind: "PRODUCT_IMAGE", mimeType: "image/jpeg", bytes: png }],
    });

    expect(draft.source).toBe("CLAUDE");
    expect(draft.model).toBe("claude-opus-5-5");
    expect(draft.payload.title).toBe("Fraîcheur nomade");

    const req = server.requests[0]!;
    expect(req.path).toBe("/v1/messages");
    expect(req.headers["x-api-key"]).toBe("sk-ant-test");
    const body = req.json() as {
      model: string;
      system: string;
      thinking?: unknown;
      temperature?: unknown;
      top_p?: unknown;
      tool_choice?: unknown;
      output_config: { effort: string; format: { type: string; schema: { properties: Record<string, unknown> } } };
      messages: { content: Array<{ type: string; text?: string }> }[];
    };
    expect(body.model).toBe("claude-opus-5-5");
    expect(body.output_config.effort).toBe("medium");
    expect(body.output_config.format.type).toBe("json_schema");
    expect(body.output_config.format.schema.properties.videoPrompt).toBeDefined();
    // Removed / forbidden on this model family:
    expect(body.thinking).toBeUndefined();
    expect(body.temperature).toBeUndefined();
    expect(body.top_p).toBeUndefined();
    expect(body.tool_choice).toBeUndefined();
    // Images come first, then the brief text.
    const content = body.messages[0]!.content;
    expect(content[0]!.type).toBe("image");
    expect(content.at(-1)!.type).toBe("text");
    expect(content.at(-1)!.text).toContain("Gourde Nomad");
    expect(content.at(-1)!.text).toContain("Image 1: product photo");
    expect(body.system).toContain("Never invent product features");
  });

  it("honours the ANTHROPIC_MODEL override", async () => {
    const generator = await setup(() => ({ json: messagesResponse(JSON.stringify(validLlmConcept)) }), "claude-sonnet-5-5");
    await generator.generate({ brief, images: [] });
    expect((server.requests[0]!.json() as { model: string }).model).toBe("claude-sonnet-5-5");
  });

  it("surfaces a refusal as a clear error instead of an empty concept", async () => {
    const generator = await setup(() => ({ json: { ...messagesResponse("", "refusal"), content: [] } }));
    await expect(generator.generate({ brief, images: [] })).rejects.toThrow(/refusé/);
  });

  it("rejects a concept that fails strict validation (prompt too long)", async () => {
    const tooLong = { ...validLlmConcept, videoPrompt: "x".repeat(2500) };
    const generator = await setup(() => ({ json: messagesResponse(JSON.stringify(tooLong)) }));
    await expect(generator.generate({ brief, images: [] })).rejects.toThrow(/concept généré est invalide/);
  });

  it("rejects a truncated response", async () => {
    const generator = await setup(() => ({ json: messagesResponse('{"title":"x"', "max_tokens") }));
    await expect(generator.generate({ brief, images: [] })).rejects.toThrow(/tronquée/);
  });

  it("translates a 401 into an actionable configuration error", async () => {
    const generator = await setup(() => ({
      status: 401,
      json: { type: "error", error: { type: "authentication_error", message: "invalid x-api-key" } },
    }));
    await expect(generator.generate({ brief, images: [] })).rejects.toMatchObject({
      code: "NOT_CONFIGURED",
      message: expect.stringContaining("ANTHROPIC_API_KEY"),
    });
  });

  it("the LLM-facing schema stays within structured-output limits (no length/range constraints)", () => {
    const json = JSON.stringify(llmConceptSchema.toJSONSchema());
    expect(json).not.toMatch(/minLength|maxLength|minimum|maximum|minItems|maxItems/);
  });
});

describe("concept service", () => {
  beforeEach(async () => {
    await resetDb();
    setConceptGeneratorForTests(templateConceptGenerator);
  });
  afterEach(() => setConceptGeneratorForTests(undefined));

  it("versions concepts per project and stores the editable prompt", async () => {
    const user = await createUser();
    const project = await createProject(user.id);
    const c1 = await generateConcept(db, user.id, project.id);
    const c2 = await generateConcept(db, user.id, project.id);
    expect([c1.version, c2.version]).toEqual([1, 2]);
    expect(c1.source).toBe("TEMPLATE");
    expect(c1.model).toBeNull();

    const latest = await getLatestConcept(db, user.id, project.id);
    expect(latest?.concept.id).toBe(c2.id);
    expect(latest?.payload?.title).toBe("Gourde Nomad");
  });

  it("is scoped by owner", async () => {
    const owner = await createUser();
    const intruder = await createUser();
    const project = await createProject(owner.id);
    await expect(generateConcept(db, intruder.id, project.id)).rejects.toMatchObject({ code: "NOT_FOUND" });
    const concept = await generateConcept(db, owner.id, project.id);
    await expect(
      updateConceptPrompt(db, intruder.id, concept.id, { videoPrompt: "x".repeat(40), negativePrompt: "" }),
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
    expect(await getLatestConcept(db, intruder.id, project.id)).toBeNull();
  });

  it("flags a user-edited prompt, and clears the flag when restored", async () => {
    const user = await createUser();
    const project = await createProject(user.id);
    const concept = await generateConcept(db, user.id, project.id);

    const edited = await updateConceptPrompt(db, user.id, concept.id, {
      videoPrompt: "A completely different, hand-written prompt for the video model.",
      negativePrompt: "blurry",
    });
    expect(edited.videoPromptEdited).toBe(true);
    expect(edited.negativePrompt).toBe("blurry");

    const restored = await updateConceptPrompt(db, user.id, concept.id, {
      videoPrompt: (concept.payload as { videoPrompt: string }).videoPrompt,
      negativePrompt: "",
    });
    expect(restored.videoPromptEdited).toBe(false);
  });

  it("validates prompt length bounds", async () => {
    const user = await createUser();
    const project = await createProject(user.id);
    const concept = await generateConcept(db, user.id, project.id);
    await expect(updateConceptPrompt(db, user.id, concept.id, { videoPrompt: "short", negativePrompt: "" })).rejects.toThrow(/trop court/);
    await expect(
      updateConceptPrompt(db, user.id, concept.id, { videoPrompt: "x".repeat(2001), negativePrompt: "" }),
    ).rejects.toThrow(/dépasse/);
  });

  it("passes product photos (resized) to the generator and skips unreadable assets", async () => {
    const user = await createUser();
    const project = await createProject(user.id);
    const big = await sharp({ create: { width: 3000, height: 2000, channels: 3, background: "#aa5500" } }).png().toBuffer();
    await getStorage().put(`assets/${project.id}/good.png`, big, { maxBytes: 50_000_000 });
    await getStorage().put(`assets/${project.id}/bad.png`, Buffer.from("not an image"), { maxBytes: 1000 });
    await db.asset.createMany({
      data: [
        { projectId: project.id, kind: "LOGO", storageKey: `assets/${project.id}/bad.png`, mimeType: "image/png", sizeBytes: 12, originalName: "bad.png" },
        { projectId: project.id, kind: "PRODUCT_IMAGE", storageKey: `assets/${project.id}/good.png`, mimeType: "image/png", sizeBytes: big.length, originalName: "good.png" },
      ],
    });

    let received: { kind: string; width: number }[] = [];
    setConceptGeneratorForTests({
      source: "TEMPLATE",
      model: null,
      async generate(input) {
        received = await Promise.all(
          input.images.map(async (i) => ({ kind: i.kind, width: (await sharp(i.bytes).metadata()).width ?? 0 })),
        );
        return templateConceptGenerator.generate(input);
      },
    });
    await generateConcept(db, user.id, project.id);
    expect(received).toEqual([{ kind: "PRODUCT_IMAGE", width: 1024 }]);
  });
});
