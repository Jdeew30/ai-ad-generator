/**
 * Errors that are safe to show to the user. Anything else is treated as an
 * unexpected failure: logged server-side and replaced by a generic message.
 */
export class AppError extends Error {
  /**
   * Brand used by `isAppError`. Next.js bundles server modules separately for
   * pages and route handlers, so the same class can exist twice at runtime and
   * `instanceof` silently fails across them; a brand check does not.
   */
  readonly __brand = "AppError" as const;

  constructor(
    message: string,
    readonly code: "NOT_FOUND" | "FORBIDDEN" | "VALIDATION" | "CONFLICT" | "NOT_CONFIGURED" | "QUOTA" | "UPSTREAM" = "VALIDATION",
  ) {
    super(message);
    this.name = "AppError";
  }
}

export function isAppError(error: unknown): error is AppError {
  return (
    error instanceof AppError ||
    (typeof error === "object" && error !== null && (error as { __brand?: unknown }).__brand === "AppError")
  );
}

export function toUserMessage(error: unknown, fallback = "Une erreur inattendue est survenue."): string {
  if (isAppError(error)) return error.message;
  console.error("[unexpected error]", error);
  return fallback;
}
