export interface ByteRange {
  start: number;
  end: number;
}

/**
 * Parses a single-range `Range: bytes=…` header against a resource of `size`
 * bytes. Returns null when no (usable) range was requested — serve the whole
 * file — or "unsatisfiable" when the range lies outside the resource (416).
 * Multi-range requests are ignored (full response), which is allowed by RFC 9110.
 */
export function parseRange(header: string | null, size: number): ByteRange | "unsatisfiable" | null {
  if (!header) return null;
  const match = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!match) return null;
  const [, rawStart, rawEnd] = match;

  if (rawStart === "" && rawEnd === "") return null;

  if (rawStart === "") {
    // Suffix range: the last N bytes.
    const length = Number(rawEnd);
    if (length === 0) return "unsatisfiable";
    return { start: Math.max(0, size - length), end: size - 1 };
  }

  const start = Number(rawStart);
  const end = rawEnd === "" ? size - 1 : Math.min(Number(rawEnd), size - 1);
  if (start >= size || start > end) return "unsatisfiable";
  return { start, end };
}
