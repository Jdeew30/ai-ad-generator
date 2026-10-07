import type { ConceptPayload } from "./schema";

export interface ConceptBrief {
  productName: string;
  description: string;
  benefits: string[];
  targetAudience: string;
  tone: string;
  visualStyle: string;
  aspectRatio: string;
  durationSeconds: number;
  language: string;
  callToAction: string | null;
  creativeNotes: string | null;
}

export interface ConceptImage {
  kind: "LOGO" | "PRODUCT_IMAGE";
  mimeType: "image/jpeg" | "image/png" | "image/webp";
  bytes: Uint8Array;
}

export interface ConceptInput {
  brief: ConceptBrief;
  images: ConceptImage[];
}

export interface ConceptDraft {
  payload: ConceptPayload;
  source: "CLAUDE" | "TEMPLATE";
  /** LLM model id when source = CLAUDE. */
  model: string | null;
}

export interface ConceptGenerator {
  readonly source: "CLAUDE" | "TEMPLATE";
  readonly model: string | null;
  generate(input: ConceptInput): Promise<ConceptDraft>;
}
