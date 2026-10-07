import { languageName, toneHint, visualStyleHint } from "@/modules/projects/presets";
import type { ConceptBrief, ConceptImage } from "./types";

export const CONCEPT_SYSTEM_PROMPT = `You are a senior creative director who writes short-form video ads for performance marketing.

You turn a product brief into a complete ad concept and, above all, ONE high-quality prompt for a text-to-video model.

Rules for the concept:
- Stay faithful to the brief. Never invent product features, claims, prices, awards or statistics that are not in the brief.
- Write every user-facing field (title, hook, summary, scene visuals, voice-over, call to action, music direction) in the language requested in the brief.
- Scene durations must add up to the target duration. Use fewer scenes for short durations (one or two for under 8 seconds).
- The hook must work in the first 1-2 seconds with no sound.

Rules for videoPrompt (the most important field):
- English, one paragraph, at most 1200 characters, describing exactly what the camera sees over the whole clip.
- Be concrete and visual: subject, setting, action, camera movement, lighting, color palette, mood, visual style.
- Describe the product as it appears in the provided images when images are given (shape, color, material, packaging); do not invent details you cannot see or read in the brief.
- Do not ask for readable text, captions, slogans, logos or watermarks inside the video: video models render text unreliably. On-screen text belongs in textOverlay and is added later in editing.
- Do not mention third-party brands, celebrities or real people by name.
- Respect the requested aspect ratio and duration in the pacing (do not describe more action than fits).
- negativePrompt lists visual defects to avoid (for example: distorted product, extra fingers, warped text, flicker, low quality). Keep it short.`;

export function renderBrief(brief: ConceptBrief, images: ConceptImage[]): string {
  const lines = [
    "<brief>",
    `Product name: ${brief.productName}`,
    `Description: ${brief.description}`,
    `Key benefits:\n${brief.benefits.map((b) => `- ${b}`).join("\n")}`,
    `Target audience: ${brief.targetAudience}`,
    `Tone: ${toneHint(brief.tone)}`,
    `Visual style: ${visualStyleHint(brief.visualStyle)}`,
    `Aspect ratio: ${brief.aspectRatio}`,
    `Target duration: ${brief.durationSeconds} seconds`,
    `Language for user-facing fields: ${languageName(brief.language)} (code: ${brief.language})`,
  ];
  if (brief.callToAction) lines.push(`Preferred call to action: ${brief.callToAction}`);
  if (brief.creativeNotes) lines.push(`Extra creative notes: ${brief.creativeNotes}`);
  lines.push("</brief>");

  if (images.length) {
    const legend = images
      .map((img, i) => `Image ${i + 1}: ${img.kind === "LOGO" ? "brand logo" : "product photo"}`)
      .join("; ");
    lines.push(`\nThe images attached before this text are: ${legend}.`);
  }
  lines.push("\nProduce the ad concept for this brief.");
  return lines.join("\n");
}
