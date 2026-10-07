/**
 * Errors that are safe to show to the user. Anything else is treated as an
 * unexpected failure: logged server-side and replaced by a generic message.
 */
export class AppError extends Error {
  constructor(
    message: string,
    readonly code: "NOT_FOUND" | "FORBIDDEN" | "VALIDATION" | "CONFLICT" | "NOT_CONFIGURED" | "QUOTA" | "UPSTREAM" = "VALIDATION",
  ) {
    super(message);
    this.name = "AppError";
  }
}

export function toUserMessage(error: unknown, fallback = "Une erreur inattendue est survenue."): string {
  if (error instanceof AppError) return error.message;
  console.error("[unexpected error]", error);
  return fallback;
}
