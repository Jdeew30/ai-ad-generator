import { isIP } from "node:net";
import type { UrlPolicy } from "./types";

/**
 * An error returned by (or while reaching) a provider API.
 * `retryable` means "nothing is wrong with the job itself; try again later"
 * (network error, 429, 5xx). Non-retryable errors are terminal for the request.
 */
export class ProviderApiError extends Error {
  constructor(
    readonly provider: string,
    readonly status: number,
    message: string,
    readonly retryable: boolean,
  ) {
    super(message);
    this.name = "ProviderApiError";
  }
}

export interface RequestOptions {
  fetch?: typeof fetch;
  timeoutMs?: number;
  signal?: AbortSignal;
  /** Extra attempts for idempotent reads only (GET). Never used for submit. */
  retries?: number;
}

const DEFAULT_TIMEOUT_MS = 30_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Pulls a human-readable message out of the error shapes the providers use. */
export function extractErrorMessage(body: unknown, fallback: string): string {
  if (typeof body === "string" && body.trim()) return body.trim().slice(0, 500);
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    const err = b.error;
    if (typeof err === "string") return err.slice(0, 500);
    if (err && typeof err === "object" && typeof (err as Record<string, unknown>).message === "string") {
      return String((err as Record<string, unknown>).message).slice(0, 500);
    }
    const detail = b.detail;
    if (typeof detail === "string") return detail.slice(0, 500);
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) => (d && typeof d === "object" ? (d as Record<string, unknown>).msg : undefined))
        .filter((m): m is string => typeof m === "string");
      if (msgs.length) return msgs.join("; ").slice(0, 500);
    }
    if (typeof b.message === "string") return b.message.slice(0, 500);
    if (typeof b.title === "string") return b.title.slice(0, 500);
  }
  return fallback;
}

/** JSON request with timeout, normalised errors and optional GET-only retries. */
export async function requestJson<T>(
  provider: string,
  url: string,
  init: RequestInit,
  options: RequestOptions = {},
): Promise<T> {
  const doFetch = options.fetch ?? fetch;
  const method = (init.method ?? "GET").toUpperCase();
  const attempts = method === "GET" ? 1 + (options.retries ?? 0) : 1;
  let lastError: ProviderApiError | undefined;

  for (let attempt = 0; attempt < attempts; attempt++) {
    if (attempt > 0) await sleep(400 * 2 ** (attempt - 1));
    try {
      const signals = [AbortSignal.timeout(options.timeoutMs ?? DEFAULT_TIMEOUT_MS)];
      if (options.signal) signals.push(options.signal);
      const res = await doFetch(url, { ...init, signal: AbortSignal.any(signals) });

      if (!res.ok) {
        const text = (await res.text().catch(() => "")).slice(0, 4000);
        let parsed: unknown = text;
        try {
          parsed = JSON.parse(text);
        } catch {
          /* keep raw text */
        }
        const message = extractErrorMessage(parsed, `HTTP ${res.status}`);
        lastError = new ProviderApiError(provider, res.status, message, res.status === 429 || res.status >= 500);
        if (!lastError.retryable) throw lastError;
        continue;
      }

      const text = await res.text();
      try {
        return JSON.parse(text) as T;
      } catch {
        throw new ProviderApiError(provider, res.status, "Réponse non-JSON du fournisseur.", true);
      }
    } catch (error) {
      if (error instanceof ProviderApiError) {
        if (!error.retryable) throw error;
        lastError = error;
        continue;
      }
      // fetch rejected: DNS/TLS/timeout/abort.
      lastError = new ProviderApiError(
        provider,
        0,
        error instanceof Error && error.name === "TimeoutError" ? "Délai dépassé en contactant le fournisseur." : "Fournisseur injoignable.",
        true,
      );
    }
  }
  throw lastError ?? new ProviderApiError(provider, 0, "Fournisseur injoignable.", true);
}

// ---------------------------------------------------------------------------
// Safe remote downloads
// ---------------------------------------------------------------------------

function isPrivateIPv4(host: string): boolean {
  const parts = host.split(".").map(Number);
  const [a, b] = parts as [number, number, number, number];
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 100 && b >= 64 && b <= 127)
  );
}

function isPrivateIPv6(host: string): boolean {
  const h = host.toLowerCase();
  if (h === "::1" || h === "::") return true;
  if (h.startsWith("fc") || h.startsWith("fd") || h.startsWith("fe8") || h.startsWith("fe9") || h.startsWith("fea") || h.startsWith("feb")) return true;
  const mapped = h.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
  return mapped ? isPrivateIPv4(mapped[1] as string) : false;
}

/**
 * Validates a URL returned by a provider before we fetch it server-side.
 * Blocks non-HTTPS and obviously internal hosts. It does not resolve DNS, so it
 * is a first line of defence rather than a complete SSRF solution.
 */
export function assertSafeRemoteUrl(raw: string, policy: UrlPolicy = "public-https"): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new ProviderApiError("url", 0, "URL de sortie invalide renvoyée par le fournisseur.", false);
  }
  if (policy === "any") return url;

  if (url.protocol !== "https:") {
    throw new ProviderApiError("url", 0, "URL de sortie non sécurisée (HTTPS requis).", false);
  }
  const host = url.hostname.replace(/^\[|\]$/g, "");
  const ipVersion = isIP(host);
  const blockedName =
    host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal");
  if (blockedName || (ipVersion === 4 && isPrivateIPv4(host)) || (ipVersion === 6 && isPrivateIPv6(host))) {
    throw new ProviderApiError("url", 0, "URL de sortie vers un hôte interne refusée.", false);
  }
  return url;
}

/**
 * GET that follows redirects manually so every hop is validated, and drops
 * credentials as soon as the origin changes.
 */
export async function fetchFollowingRedirects(
  startUrl: string,
  init: { headers?: Record<string, string>; signal?: AbortSignal },
  options: { fetch?: typeof fetch; policy?: UrlPolicy; maxRedirects?: number; provider: string; timeoutMs?: number },
): Promise<Response> {
  const doFetch = options.fetch ?? fetch;
  let url = assertSafeRemoteUrl(startUrl, options.policy);
  const origin = url.origin;
  let headers = init.headers ?? {};
  const maxRedirects = options.maxRedirects ?? 3;

  for (let hop = 0; hop <= maxRedirects; hop++) {
    const signals = [AbortSignal.timeout(options.timeoutMs ?? 120_000)];
    if (init.signal) signals.push(init.signal);
    let res: Response;
    try {
      res = await doFetch(url, { method: "GET", headers, redirect: "manual", signal: AbortSignal.any(signals) });
    } catch {
      throw new ProviderApiError(options.provider, 0, "Téléchargement impossible (fournisseur injoignable).", true);
    }

    if (res.status >= 300 && res.status < 400) {
      const location = res.headers.get("location");
      await res.body?.cancel().catch(() => undefined);
      if (!location) throw new ProviderApiError(options.provider, res.status, "Redirection sans destination.", false);
      url = assertSafeRemoteUrl(new URL(location, url).toString(), options.policy);
      if (url.origin !== origin) headers = {};
      continue;
    }

    if (!res.ok) {
      await res.body?.cancel().catch(() => undefined);
      const retryable = res.status === 429 || res.status >= 500;
      throw new ProviderApiError(
        options.provider,
        res.status,
        retryable ? `Téléchargement temporairement indisponible (HTTP ${res.status}).` : `Le fichier n'est plus disponible chez le fournisseur (HTTP ${res.status}).`,
        retryable,
      );
    }
    return res;
  }
  throw new ProviderApiError(options.provider, 0, "Trop de redirections.", false);
}
