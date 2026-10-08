import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import opentype, { type Font, type PathCommand } from "opentype.js";

/**
 * Text is converted to vector outlines (SVG paths) from a bundled font file
 * instead of asking the OS to draw it. That makes the output identical on
 * Windows, macOS and Linux and independent of whatever fonts are installed.
 */

export type FontWeight = "Regular" | "Medium" | "SemiBold" | "Bold";

const fonts = new Map<FontWeight, Promise<Font>>();

const GEIST_DIR = path.join("node_modules", "geist", "dist", "fonts", "geist-sans");

/**
 * Finds `node_modules/geist/.../Geist-<weight>.ttf` by walking up from the working
 * directory (works with hoisted/monorepo installs). `geist` does not export its
 * package.json, so `require.resolve` cannot be used.
 */
function fontPath(weight: FontWeight): string {
  const file = `Geist-${weight}.ttf`;
  if (process.env.MONTAGE_FONT_DIR) return path.join(process.env.MONTAGE_FONT_DIR, file);
  let dir = process.cwd();
  for (;;) {
    const candidate = path.join(dir, GEIST_DIR, file);
    if (existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`Police ${file} introuvable (installez les dépendances avec « npm install » ou définissez MONTAGE_FONT_DIR).`);
}

export function loadFont(weight: FontWeight): Promise<Font> {
  let cached = fonts.get(weight);
  if (!cached) {
    cached = readFile(fontPath(weight)).then((buf) =>
      opentype.parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer),
    );
    // Do not cache a failure forever.
    cached.catch(() => fonts.delete(weight));
    fonts.set(weight, cached);
  }
  return cached;
}

/** Drops characters the font cannot draw (emoji, CJK…) so we never render "tofu" boxes. */
export function sanitizeForFont(font: Font, text: string): string {
  let out = "";
  for (const ch of text.normalize("NFC")) {
    if (/\s/.test(ch)) out += " ";
    else if (font.charToGlyphIndex(ch) > 0) out += ch;
  }
  return out.replace(/\s+/g, " ").trim();
}

/** Greedy word wrap by measured width; over-long words are split by character. */
export function wrapText(font: Font, text: string, fontSize: number, maxWidth: number, maxLines = 3): string[] {
  const width = (s: string) => font.getAdvanceWidth(s, fontSize, { kerning: true });
  const words = text.split(" ").filter(Boolean);
  const lines: string[] = [];
  let current = "";

  const pushWord = (word: string) => {
    if (width(word) <= maxWidth) {
      current = word;
      return;
    }
    // Split a single word that is wider than the box.
    let chunk = "";
    for (const ch of word) {
      if (width(chunk + ch) > maxWidth && chunk) {
        lines.push(chunk);
        chunk = ch;
      } else {
        chunk += ch;
      }
    }
    current = chunk;
  };

  for (const word of words) {
    if (!current) pushWord(word);
    else if (width(`${current} ${word}`) <= maxWidth) current = `${current} ${word}`;
    else {
      lines.push(current);
      current = "";
      pushWord(word);
    }
  }
  if (current) lines.push(current);

  if (lines.length <= maxLines) return lines;
  const kept = lines.slice(0, maxLines);
  let last = kept[maxLines - 1] ?? "";
  while (last && width(`${last}…`) > maxWidth) last = last.slice(0, -1).trimEnd();
  kept[maxLines - 1] = `${last}…`;
  return kept;
}

/**
 * Serialises glyph outlines to SVG path data. opentype.js's own `toPathData()` can
 * emit the literal "NaN" for some offsets, and the SVG renderer then silently stops
 * reading the line (words vanish from the ad), so we format the numbers ourselves and
 * refuse to produce an invalid path.
 */
export function pathData(commands: PathCommand[], decimals = 2): string {
  const n = (v: number | undefined): string => {
    if (v === undefined || !Number.isFinite(v)) throw new Error("Tracé de police invalide (coordonnée non finie).");
    return String(Number(v.toFixed(decimals)));
  };
  return commands
    .map((c) => {
      switch (c.type) {
        case "M":
        case "L":
          return `${c.type}${n(c.x)} ${n(c.y)}`;
        case "Q":
          return `Q${n(c.x1)} ${n(c.y1)} ${n(c.x)} ${n(c.y)}`;
        case "C":
          return `C${n(c.x1)} ${n(c.y1)} ${n(c.x2)} ${n(c.y2)} ${n(c.x)} ${n(c.y)}`;
        default:
          return "Z";
      }
    })
    .join("");
}

export interface TypesetBlock {
  /** SVG <path> elements (already positioned). */
  svg: string;
  /** Total height of the block in px. */
  height: number;
}

/**
 * Typesets centred lines of text as SVG paths. `baselineTop` is the y of the first
 * line's baseline.
 */
export function typesetCentered(
  font: Font,
  lines: string[],
  opts: { centerX: number; baselineTop: number; fontSize: number; lineHeight: number; fill: string },
): TypesetBlock {
  const paths = lines.map((line, i) => {
    const w = font.getAdvanceWidth(line, opts.fontSize, { kerning: true });
    const x = opts.centerX - w / 2;
    const y = opts.baselineTop + i * opts.lineHeight;
    const d = pathData(font.getPath(line, x, y, opts.fontSize, { kerning: true }).commands);
    return `<path d="${d}" fill="${opts.fill}"/>`;
  });
  return { svg: paths.join(""), height: Math.max(0, lines.length - 1) * opts.lineHeight + opts.fontSize };
}
