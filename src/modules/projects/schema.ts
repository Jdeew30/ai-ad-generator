import { z } from "zod";
import {
  ASPECT_RATIOS,
  DURATIONS,
  LANGUAGE_IDS,
  TONE_IDS,
  VISUAL_STYLE_IDS,
  type AspectRatio,
  type LanguageId,
  type ToneId,
  type VisualStyleId,
} from "./presets";

const trimmed = (min: number, max: number, label: string) =>
  z
    .string()
    .trim()
    .min(min, `${label} : ${min} caractères minimum`)
    .max(max, `${label} : ${max} caractères maximum`);

const optionalText = (max: number, label: string) =>
  z.preprocess(
    (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
    z.string().trim().max(max, `${label} : ${max} caractères maximum`).optional(),
  );

/** Shared by the client form and the server actions (single source of truth). */
export const briefSchema = z.object({
  productName: trimmed(2, 80, "Nom du produit"),
  description: trimmed(10, 1200, "Description"),
  benefits: z
    .array(trimmed(2, 120, "Avantage"))
    .min(1, "Ajoutez au moins un avantage")
    .max(6, "6 avantages maximum"),
  targetAudience: trimmed(5, 400, "Cible"),
  tone: z.enum(TONE_IDS),
  visualStyle: z.enum(VISUAL_STYLE_IDS),
  aspectRatio: z.enum(ASPECT_RATIOS),
  durationSeconds: z.coerce
    .number()
    .refine((n) => (DURATIONS as readonly number[]).includes(n), "Durée non supportée"),
  language: z.enum(LANGUAGE_IDS),
  callToAction: optionalText(80, "Appel à l'action"),
  creativeNotes: optionalText(600, "Notes créatives"),
});

export type Brief = z.infer<typeof briefSchema>;

/** Shape of the form state (what the user types), before `briefSchema` cleans it. */
export interface BriefInput {
  productName: string;
  description: string;
  benefits: string[];
  targetAudience: string;
  tone: ToneId;
  visualStyle: VisualStyleId;
  aspectRatio: AspectRatio;
  durationSeconds: number;
  language: LanguageId;
  callToAction: string;
  creativeNotes: string;
}

export const DEFAULT_BRIEF: BriefInput = {
  productName: "",
  description: "",
  benefits: [""],
  targetAudience: "",
  tone: "premium",
  visualStyle: "cinematic",
  aspectRatio: "9:16",
  durationSeconds: 8,
  language: "fr",
  callToAction: "",
  creativeNotes: "",
};
