import { getEnv } from "@/lib/env";
import { createAnthropicConceptGenerator, DEFAULT_ANTHROPIC_MODEL } from "./anthropic";
import { templateConceptGenerator } from "./template";
import type { ConceptGenerator } from "./types";

export type { ConceptGenerator, ConceptInput, ConceptDraft } from "./types";
export { conceptSchema, type ConceptPayload } from "./schema";

const globalForConcepts = globalThis as unknown as { __conceptGenerator?: ConceptGenerator };

/** Claude when a key is configured, otherwise the clearly-labelled non-AI template. */
export function getConceptGenerator(): ConceptGenerator {
  if (globalForConcepts.__conceptGenerator) return globalForConcepts.__conceptGenerator;
  const env = getEnv();
  return env.ANTHROPIC_API_KEY
    ? createAnthropicConceptGenerator({
        apiKey: env.ANTHROPIC_API_KEY,
        model: env.ANTHROPIC_MODEL,
        baseUrl: env.ANTHROPIC_BASE_URL,
      })
    : templateConceptGenerator;
}

/** Test seam. */
export function setConceptGeneratorForTests(generator: ConceptGenerator | undefined): void {
  globalForConcepts.__conceptGenerator = generator;
}

export function describeConceptEngine(): { source: "CLAUDE" | "TEMPLATE"; model: string | null } {
  const env = getEnv();
  if (env.ANTHROPIC_API_KEY) return { source: "CLAUDE", model: env.ANTHROPIC_MODEL ?? DEFAULT_ANTHROPIC_MODEL };
  return { source: "TEMPLATE", model: null };
}
