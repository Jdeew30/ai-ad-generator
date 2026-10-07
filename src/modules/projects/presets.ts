/**
 * Creative presets. `id`s are what is stored in the database; `label` is shown
 * in the UI (French); `hint` is an English description injected in prompts
 * (video models and LLMs respond best to English direction).
 */

export const ASPECT_RATIOS = ["9:16", "16:9", "1:1"] as const;
export type AspectRatio = (typeof ASPECT_RATIOS)[number];

export const ASPECT_RATIO_META: Record<AspectRatio, { label: string; usage: string }> = {
  "9:16": { label: "Vertical 9:16", usage: "TikTok, Reels, Shorts, Stories" },
  "16:9": { label: "Horizontal 16:9", usage: "YouTube, site web, TV" },
  "1:1": { label: "Carré 1:1", usage: "Feed Instagram, Facebook, LinkedIn" },
};

/** Target clip lengths, in seconds. Models only support some of them (see model profiles). */
export const DURATIONS = [4, 5, 6, 8, 10, 12] as const;

export const TONES = [
  { id: "premium", label: "Premium & élégant", hint: "premium, refined, confident, understated luxury" },
  { id: "energetic", label: "Dynamique & énergique", hint: "energetic, fast-paced, upbeat, punchy" },
  { id: "friendly", label: "Chaleureux & accessible", hint: "warm, friendly, approachable, authentic" },
  { id: "bold", label: "Audacieux & percutant", hint: "bold, disruptive, high-contrast, attention-grabbing" },
  { id: "humorous", label: "Humoristique", hint: "playful, witty, lightly humorous" },
  { id: "minimal", label: "Épuré & sobre", hint: "calm, minimal, clean, lots of negative space" },
  { id: "emotional", label: "Émotionnel", hint: "emotional, human, heartfelt, storytelling-driven" },
] as const;

export const VISUAL_STYLES = [
  { id: "cinematic", label: "Cinématique", hint: "cinematic look, shallow depth of field, anamorphic feel, dramatic lighting, smooth camera moves" },
  { id: "studio", label: "Studio produit", hint: "clean studio product shoot, seamless backdrop, soft diffused lighting, crisp reflections, slow macro camera moves" },
  { id: "lifestyle", label: "Lifestyle naturel", hint: "natural lifestyle footage, real-life setting, soft daylight, handheld warmth" },
  { id: "ugc", label: "UGC / smartphone", hint: "authentic user-generated style, vertical smartphone footage, handheld, natural light, casual framing" },
  { id: "motion3d", label: "3D / motion design", hint: "polished 3D motion design, floating product, smooth procedural animation, studio-grade render" },
  { id: "minimalist", label: "Minimaliste", hint: "minimalist composition, monochrome palette, geometric shapes, precise slow movement" },
  { id: "neon", label: "Néon futuriste", hint: "futuristic neon lighting, dark environment, glossy reflections, light trails" },
] as const;

export const LANGUAGES = [
  { id: "fr", label: "Français" },
  { id: "en", label: "English" },
  { id: "es", label: "Español" },
  { id: "de", label: "Deutsch" },
  { id: "it", label: "Italiano" },
  { id: "pt", label: "Português" },
] as const;

export type ToneId = (typeof TONES)[number]["id"];
export type VisualStyleId = (typeof VISUAL_STYLES)[number]["id"];
export type LanguageId = (typeof LANGUAGES)[number]["id"];

export const TONE_IDS = TONES.map((t) => t.id) as [ToneId, ...ToneId[]];
export const VISUAL_STYLE_IDS = VISUAL_STYLES.map((s) => s.id) as [VisualStyleId, ...VisualStyleId[]];
export const LANGUAGE_IDS = LANGUAGES.map((l) => l.id) as [LanguageId, ...LanguageId[]];

export function toneHint(id: string): string {
  return TONES.find((t) => t.id === id)?.hint ?? id;
}

export function visualStyleHint(id: string): string {
  return VISUAL_STYLES.find((s) => s.id === id)?.hint ?? id;
}

export function languageName(id: string): string {
  return LANGUAGES.find((l) => l.id === id)?.label ?? id;
}
