import { MAX_PROMPT_CHARS } from "@/modules/video-providers/types";
import { toneHint, visualStyleHint } from "@/modules/projects/presets";
import type { ConceptDraft, ConceptGenerator, ConceptInput } from "./types";
import type { ConceptPayload } from "./schema";

const clip = (text: string, max: number) => (text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}…`);

/**
 * Deterministic, NON-AI compilation of the brief into a video prompt.
 * It does no creative writing (no hook, no voice-over, no copy) and is always
 * labelled as a template in the UI. Its only job is to let a user with video
 * credentials but no LLM key still get a coherent, editable prompt.
 */
export const templateConceptGenerator: ConceptGenerator = {
  source: "TEMPLATE",
  model: null,

  async generate(input: ConceptInput): Promise<ConceptDraft> {
    const { brief } = input;
    const hasProductImage = input.images.some((i) => i.kind === "PRODUCT_IMAGE");

    const moments = brief.benefits.slice(0, 3).map((b) => clip(b, 120));
    const parts = [
      `${visualStyleHint(brief.visualStyle)}.`,
      `Product advertisement for "${brief.productName}": ${clip(brief.description, 320)}`,
      moments.length ? `Show, one after the other: ${moments.join("; ")}.` : "",
      `Mood: ${toneHint(brief.tone)}.`,
      `Audience: ${clip(brief.targetAudience, 160)}.`,
      brief.creativeNotes ? `Direction: ${clip(brief.creativeNotes, 240)}.` : "",
      "Keep the product clearly visible and consistent throughout the clip, with smooth camera motion.",
      "No readable text, captions, logos or watermarks in the frame.",
    ].filter(Boolean);

    const videoPrompt = clip(parts.join(" "), MAX_PROMPT_CHARS);

    const sceneCount = brief.durationSeconds >= 8 ? 3 : brief.durationSeconds >= 5 ? 2 : 1;
    const base = Math.floor(brief.durationSeconds / sceneCount);
    const scenes: ConceptPayload["scenes"] = Array.from({ length: sceneCount }, (_, i) => ({
      durationSeconds: i === sceneCount - 1 ? brief.durationSeconds - base * (sceneCount - 1) : base,
      visual:
        i === sceneCount - 1
          ? `Closing shot of ${brief.productName}`
          : (moments[i] ?? `Presentation of ${brief.productName}`),
      camera: "",
      textOverlay: "",
      voiceover: "",
    }));

    return {
      source: "TEMPLATE",
      model: null,
      payload: {
        title: brief.productName,
        hook: "",
        summary: hasProductImage
          ? "Prompt assemblé automatiquement à partir du brief (aucune IA). Les images produit ne sont pas analysées en mode gabarit."
          : "Prompt assemblé automatiquement à partir du brief (aucune IA).",
        scenes,
        voiceoverScript: "",
        callToAction: brief.callToAction ?? "",
        musicMood: "",
        videoPrompt,
        negativePrompt: "distorted product, warped or unreadable text, extra limbs, flicker, low quality, watermark",
      },
    };
  },
};
