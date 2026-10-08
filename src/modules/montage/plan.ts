import { FPS, type MontageSpec, type SceneText, type ScenePlan } from "./types";

/** Longest line the layout can typeset legibly; longer input is clipped with an ellipsis. */
export const MAX_LINE_CHARS = 90;

export function clip(text: string, max = MAX_LINE_CHARS): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length <= max ? t : `${t.slice(0, max - 1).trimEnd()}…`;
}

/**
 * Pure function: turns the brief into a scene-by-scene timeline.
 *
 * - scene 0 shows the product name, middle scenes show one benefit each (with two scenes, the
 *   first benefit goes under the name), the last
 *   scene is an end card with the call to action (and the logo, if provided);
 * - frame counts add up to exactly `durationSeconds * FPS`, so the final file has
 *   the requested length.
 */
export function planTimeline(spec: MontageSpec): ScenePlan[] {
  const totalFrames = Math.round(spec.durationSeconds * FPS);
  const sceneCount = Math.max(1, Math.min(5, Math.floor(spec.durationSeconds / 2)));

  const headline = clip(spec.headline, 60);
  const benefits = spec.captions.map((c) => clip(c)).filter(Boolean).slice(0, 3);
  const cta = spec.callToAction ? clip(spec.callToAction, 60) : "";

  const textsFor = (i: number): { texts: SceneText[]; isEndCard: boolean } => {
    if (sceneCount === 1) {
      const sub = cta || benefits[0];
      return { texts: [{ text: headline, style: "title" }, ...(sub ? [{ text: sub, style: "body" as const }] : [])], isEndCard: false };
    }
    if (i === 0) {
      // With only two scenes there is no middle scene: show the first benefit under the name.
      const sub = sceneCount === 2 ? benefits[0] : undefined;
      return { texts: [{ text: headline, style: "title" }, ...(sub ? [{ text: sub, style: "body" as const }] : [])], isEndCard: false };
    }
    if (i === sceneCount - 1) {
      return {
        texts: [
          { text: cta || headline, style: "cta" },
          ...(cta ? [{ text: headline, style: "body" as const }] : []),
        ],
        isEndCard: true,
      };
    }
    const benefit = benefits[i - 1];
    return { texts: benefit ? [{ text: benefit, style: "body" }] : [], isEndCard: false };
  };

  const base = Math.floor(totalFrames / sceneCount);
  return Array.from({ length: sceneCount }, (_, i) => {
    const { texts, isEndCard } = textsFor(i);
    return {
      index: i,
      imageIndex: i % spec.images.length,
      frames: i === sceneCount - 1 ? totalFrames - base * (sceneCount - 1) : base,
      zoom: i % 2 === 0 ? "in" : "out",
      texts,
      isEndCard,
    };
  });
}

export interface PlanSummary {
  scenes: number;
  photosShown: number;
  benefitsShown: number;
  logoShown: boolean;
}

/**
 * What a montage of this length will really contain. Used both for the stored
 * description of a generation and for the notice in the UI, so neither can promise
 * more than the video shows. Pure and dependency-free (safe in client components).
 */
export function summarizePlan(input: {
  durationSeconds: number;
  photoCount: number;
  benefitCount: number;
  hasLogo: boolean;
  hasCallToAction: boolean;
}): PlanSummary {
  const benefits = Array.from({ length: Math.min(3, input.benefitCount) }, (_, i) => `benefit-${i}`);
  const plan = planTimeline({
    aspectRatio: "9:16",
    durationSeconds: input.durationSeconds,
    images: Array.from({ length: Math.max(1, input.photoCount) }, () => new Uint8Array(0)),
    headline: "headline",
    captions: benefits,
    callToAction: input.hasCallToAction ? "cta" : undefined,
  });
  const drawn = new Set(plan.flatMap((s) => s.texts.map((t) => t.text)));
  return {
    scenes: plan.length,
    photosShown: new Set(plan.map((s) => s.imageIndex)).size,
    benefitsShown: benefits.filter((b) => drawn.has(b)).length,
    logoShown: input.hasLogo && plan.some((s) => s.isEndCard),
  };
}
