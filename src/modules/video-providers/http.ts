import { isIP } from "node:net";
import type { UrlPolicy } from "./types";

/**
 * An error returned by (or while reaching) a provider API.
 * `retryable` means "nothing is wrong with the job itself; try again later"
 * (network error, 429, 5xx). Non-retryable errors are terminal for the request.
 */
export class ProviderApiError extends Error {
  readonly __brand = "ProviderApiError" as const;

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

/** Brand-based check (see AppError): robust to Next.js bundling a module twice. */
export function isProviderApiError(error: unknown): error is ProviderApiError {
  return (
    error instanceof ProviderApiError ||
    (typeof error === "object" && error !== null && (error as { __brand?: unknown }).__brand === "ProviderApiError")
  );
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
      if (isProviderApiError(error)) {
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

/** Expands an IPv6 literal into its 8 16-bit groups (handles `::` and a trailing dotted quad). */
function expandIPv6(host: string): number[] | null {
  let h = host.toLowerCase();
  const dotted = h.match(/(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (dotted) {
    const [a, b, c, d] = dotted.slice(1).map(Number) as [number, number, number, number];
    h = h.slice(0, dotted.index) + ((a << 8) | b).toString(16) + ":" + ((c << 8) | d).toString(16);
  }
  const halves = h.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const missing = 8 - head.length - tail.length;
  if ((halves.length === 1 && missing !== 0) || missing < 0) return null;
  const groups = [...head, ...Array<string>(halves.length === 2 ? missing : 0).fill("0"), ...tail].map((g) => parseInt(g, 16));
  return groups.length === 8 && groups.every((g) => Number.isInteger(g) && g >= 0 && g <= 0xffff) ? groups : null;
}

function embeddedIPv4(hi: number, lo: number): string {
  return `${hi >> 8}.${hi & 255}.${lo >> 8}.${lo & 255}`;
}

function isPrivateIPv6(host: string): boolean {
  const g = expandIPv6(host);
  if (!g) return true; // unparseable: fail closed
  const [g0, g1, g2, g3, g4, g5, g6, g7] = g as [number, number, number, number, number, number, number, number];

  // :: (unspecified), ::1 (loopback) and the deprecated IPv4-compatible ::a.b.c.d
  if (g0 === 0 && g1 === 0 && g2 === 0 && g3 === 0 && g4 === 0 && g5 === 0) return true;
  // IPv4-mapped ::ffff:a.b.c.d — the URL parser prints these in hex, so decode them.
  if (g0 === 0 && g1 === 0 && g2 === 0 && g3 === 0 && g4 === 0 && g5 === 0xffff) return isPrivateIPv4(embeddedIPv4(g6, g7));
  // NAT64 64:ff9b::/96 embeds an IPv4 address.
  if (g0 === 0x64 && g1 === 0xff9b && g2 === 0 && g3 === 0 && g4 === 0 && g5 === 0) return isPrivateIPv4(embeddedIPv4(g6, g7));
  // 6to4 2002::/16 embeds an IPv4 address in the next 32 bits.
  if (g0 === 0x2002) return isPrivateIPv4(embeddedIPv4(g1, g2));
  // fc00::/7 unique-local, fe80::/10 link-local, fec0::/10 deprecated site-local
  if ((g0 & 0xfe00) === 0xfc00 || (g0 & 0xffc0) === 0xfe80 || (g0 & 0xffc0) === 0xfec0) return true;
  return false;
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
