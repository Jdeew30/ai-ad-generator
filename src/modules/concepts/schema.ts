import { z } from "zod";
import { MAX_PROMPT_CHARS } from "@/modules/video-providers/types";

/**
 * Shape requested from the LLM (sent as a JSON schema via structured outputs).
 * Kept free of length/range constraints on purpose — structured outputs only
 * support a subset of JSON Schema — and validated strictly afterwards by
 * `conceptSchema`.
 */
export const llmConceptSchema = z.object({
  title: z.string().describe("Short campaign title, in the brief's language"),
  hook: z.string().describe("The first 1-2 seconds: what stops the scroll, in the brief's language"),
  summary: z.string().describe("2-3 sentence creative idea, in the brief's language"),
  scenes: z
    .array(
      z.object({
        durationSeconds: z.number().describe("Length of this scene in seconds"),
        visual: z.string().describe("What we see, concretely, in the brief's language"),
        camera: z.string().describe("Camera movement and framing, in the brief's language"),
        textOverlay: z.string().describe("Short on-screen text to add in post-production, or an empty string"),
        voiceover: z.string().describe("Voice-over line for this scene in the brief's language, or an empty string"),
      }),
    )
    .describe("1 to 5 scenes whose durations add up to the target duration"),
  voiceoverScript: z.string().describe("Full voice-over script, in the brief's language"),
  callToAction: z.string().describe("Closing call to action, in the brief's language"),
  musicMood: z.string().describe("Music / sound direction, in the brief's language"),
  videoPrompt: z
    .string()
    .describe("ONE consolidated prompt for a text-to-video model, in English, at most 1200 characters"),
  negativePrompt: z.string().describe("Things to avoid visually, in English, or an empty string"),
});

const bounded = (max: number, label: string) =>
  z.string().trim().max(max, `${label} trop long (${max} caractères maximum)`);

/** Strict validation applied to anything we store or display, whatever its origin. */
export const conceptSchema = z.object({
  title: bounded(120, "Titre").min(1),
  hook: bounded(400, "Accroche"),
  summary: bounded(1200, "Résumé"),
  scenes: z
    .array(
      z.object({
        durationSeconds: z.number().positive().max(60),
        visual: bounded(600, "Visuel").min(1),
        camera: bounded(300, "Caméra"),
        textOverlay: bounded(160, "Texte à l'écran"),
        voiceover: bounded(400, "Voix off"),
      }),
    )
    .min(1)
    .max(6),
  voiceoverScript: bounded(1500, "Script voix off"),
  callToAction: bounded(200, "Appel à l'action"),
  musicMood: bounded(300, "Ambiance musicale"),
  videoPrompt: bounded(MAX_PROMPT_CHARS, "Prompt vidéo").min(20, "Prompt vidéo trop court"),
  negativePrompt: bounded(600, "Prompt négatif"),
});

export type ConceptPayload = z.infer<typeof conceptSchema>;
