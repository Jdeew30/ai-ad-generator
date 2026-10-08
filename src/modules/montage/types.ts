export type MontageAspect = "9:16" | "16:9" | "1:1";

/**
 * Everything the local montage engine needs. This is NOT generative AI: it
 * animates the user's own photos and typesets the brief's own words.
 */
export interface MontageSpec {
  aspectRatio: MontageAspect;
  /** Total length in seconds (4–12). */
  durationSeconds: number;
  /** 1–5 product photos, already validated image bytes (PNG/JPEG/WebP). */
  images: Uint8Array[];
  logo?: Uint8Array;
  /** Product name. */
  headline: string;
  /** Up to 3 short benefit lines. */
  captions: string[];
  callToAction?: string;
}

export interface SceneText {
  text: string;
  style: "title" | "body" | "cta";
}

export interface ScenePlan {
  index: number;
  imageIndex: number;
  frames: number;
  /** Ken Burns direction. */
  zoom: "in" | "out";
  /** Text lines typeset over this scene. */
  texts: SceneText[];
  /** Last scene: dark end card with the logo (if any). */
  isEndCard: boolean;
}

export const FPS = 30;

export const CANVAS: Record<MontageAspect, { w: number; h: number }> = {
  "9:16": { w: 1080, h: 1920 },
  "16:9": { w: 1920, h: 1080 },
  "1:1": { w: 1080, h: 1080 },
};
