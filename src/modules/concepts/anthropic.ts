import Anthropic from "@anthropic-ai/sdk";
import { zodOutputFormat } from "@anthropic-ai/sdk/helpers/zod";
import { AppError } from "@/lib/errors";
import { CONCEPT_SYSTEM_PROMPT, renderBrief } from "./prompt";
import { conceptSchema, llmConceptSchema } from "./schema";
import type { ConceptDraft, ConceptGenerator, ConceptInput } from "./types";

/** Default per the Claude API skill; override with ANTHROPIC_MODEL (Opus/Sonnet 5.x family). */
export const DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5";

const MAX_IMAGES = 3;

export interface AnthropicConceptOptions {
  apiKey: string;
  model?: string;
  baseUrl?: string;
}

/**
 * Real LLM-backed concept generation. Uses the official SDK with a Zod-derived
 * JSON schema (structured outputs), then validates the result with the strict
 * `conceptSchema` before anything is stored.
 *
 * Notes on the request shape (Claude Opus 5.5): thinking is always on, so the
 * `thinking` parameter is omitted; no sampling parameters are sent; `effort` is
 * set explicitly because this model defaults to `medium`.
 */
export function createAnthropicConceptGenerator(options: AnthropicConceptOptions): ConceptGenerator {
  const model = options.model ?? DEFAULT_ANTHROPIC_MODEL;
  const client = new Anthropic({
    apiKey: options.apiKey,
    baseURL: options.baseUrl,
    maxRetries: 2,
    timeout: 120_000,
  });

  return {
    source: "CLAUDE",
    model,

    async generate(input: ConceptInput): Promise<ConceptDraft> {
      const images = input.images.slice(0, MAX_IMAGES);
      const content: Anthropic.ContentBlockParam[] = [
        ...images.map(
          (img): Anthropic.ImageBlockParam => ({
            type: "image",
            source: { type: "base64", media_type: img.mimeType, data: Buffer.from(img.bytes).toString("base64") },
          }),
        ),
        { type: "text", text: renderBrief(input.brief, images) },
      ];

      // `create` (not `parse`) on purpose: `parse` throws its own error on truncated
      // JSON before we can look at stop_reason, and we want to branch in this order:
      // refusal -> truncation -> JSON -> strict schema.
      let response;
      try {
        response = await client.messages.create({
          model,
          max_tokens: 16000,
          system: CONCEPT_SYSTEM_PROMPT,
          messages: [{ role: "user", content }],
          output_config: { effort: "medium", format: zodOutputFormat(llmConceptSchema) },
        });
      } catch (error) {
        throw translateAnthropicError(error);
      }

      if (response.stop_reason === "refusal") {
        throw new AppError(
          "Le modèle a refusé de générer ce concept (contenu jugé sensible). Reformulez le brief ou utilisez le gabarit.",
          "UPSTREAM",
        );
      }
      if (response.stop_reason === "max_tokens") {
        throw new AppError("La réponse du modèle a été tronquée. Réessayez.", "UPSTREAM");
      }

      const text = response.content.find((b): b is Anthropic.TextBlock => b.type === "text")?.text;
      let raw: unknown;
      try {
        raw = text ? JSON.parse(text) : undefined;
      } catch {
        raw = undefined;
      }
      if (raw === undefined) {
        throw new AppError("Le modèle n'a pas renvoyé un concept exploitable. Réessayez.", "UPSTREAM");
      }

      const checked = conceptSchema.safeParse(raw);
      if (!checked.success) {
        const issue = checked.error.issues[0];
        throw new AppError(
          `Le concept généré est invalide (${issue?.path.join(".") || "?"} : ${issue?.message ?? "format"}). Réessayez.`,
          "UPSTREAM",
        );
      }
      return { payload: checked.data, source: "CLAUDE", model };
    },
  };
}

function translateAnthropicError(error: unknown): AppError {
  if (error instanceof Anthropic.AuthenticationError) {
    return new AppError("Clé API Anthropic refusée (ANTHROPIC_API_KEY invalide).", "NOT_CONFIGURED");
  }
  if (error instanceof Anthropic.PermissionDeniedError) {
    return new AppError("Accès refusé par Anthropic pour ce modèle ou cette clé.", "NOT_CONFIGURED");
  }
  if (error instanceof Anthropic.RateLimitError) {
    return new AppError("Limite de débit Anthropic atteinte. Réessayez dans un instant.", "UPSTREAM");
  }
  if (error instanceof Anthropic.NotFoundError) {
    return new AppError("Modèle Anthropic introuvable (vérifiez ANTHROPIC_MODEL).", "NOT_CONFIGURED");
  }
  if (error instanceof Anthropic.BadRequestError) {
    return new AppError(`Requête refusée par Anthropic : ${error.message}`, "UPSTREAM");
  }
  if (error instanceof Anthropic.APIConnectionError) {
    return new AppError("Anthropic est injoignable. Réessayez.", "UPSTREAM");
  }
  if (error instanceof Anthropic.APIError) {
    return new AppError(`Erreur Anthropic (${error.status ?? "?"}). Réessayez.`, "UPSTREAM");
  }
  throw error;
}
