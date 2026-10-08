import sharp, { type OverlayOptions } from "sharp";
import { CANVAS, type MontageAspect, type SceneText } from "./types";
import { loadFont, sanitizeForFont, typesetCentered, wrapText } from "./text";

/** Scene stills are rendered larger than the video so the slow zoom stays smooth. */
export const BASE_SCALE = 1.5;

const MAX_INPUT_PIXELS = 50_000_000;

interface Layout {
  w: number;
  h: number;
  short: number;
  photo: { cx: number; cy: number; maxW: number; maxH: number };
  /** Distance from the bottom edge to the last text baseline (keeps clear of platform UI). */
  textBottom: number;
  textMaxW: number;
}

function layoutFor(aspect: MontageAspect): Layout {
  const { w, h } = CANVAS[aspect];
  const short = Math.min(w, h);
  switch (aspect) {
    case "9:16":
      return { w, h, short, photo: { cx: w / 2, cy: h * 0.37, maxW: w * 0.82, maxH: h * 0.5 }, textBottom: h * 0.12, textMaxW: w * 0.84 };
    case "16:9":
      return { w, h, short, photo: { cx: w / 2, cy: h * 0.4, maxW: w * 0.5, maxH: h * 0.62 }, textBottom: h * 0.1, textMaxW: w * 0.72 };
    default:
      return { w, h, short, photo: { cx: w / 2, cy: h * 0.4, maxW: w * 0.62, maxH: h * 0.5 }, textBottom: h * 0.09, textMaxW: w * 0.84 };
  }
}

function roundedRectSvg(w: number, h: number, r: number, extra = ""): Buffer {
  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><rect width="${w}" height="${h}" rx="${r}" ry="${r}" fill="#fff" ${extra}/></svg>`,
  );
}

async function blurredBackground(image: Uint8Array, w: number, h: number, brightness: number): Promise<Buffer> {
  // Blur is low-frequency: compute it on a quarter-size copy, then scale back up (much faster, same look).
  const small = await sharp(image, { limitInputPixels: MAX_INPUT_PIXELS })
    .rotate()
    .flatten({ background: "#111111" })
    .resize(Math.round(w / 4), Math.round(h / 4), { fit: "cover" })
    .blur(9)
    .modulate({ brightness, saturation: 1.15 })
    .toBuffer();
  return sharp(small).resize(w, h, { kernel: "cubic" }).png({ compressionLevel: 1 }).toBuffer();
}

/** Photo scaled to fit the box, with rounded corners. */
async function roundedPhoto(image: Uint8Array, maxW: number, maxH: number, radius: number) {
  const fitted = await sharp(image, { limitInputPixels: MAX_INPUT_PIXELS })
    .rotate()
    .flatten({ background: "#ffffff" })
    .resize({ width: Math.round(maxW), height: Math.round(maxH), fit: "inside" })
    .png()
    .toBuffer({ resolveWithObject: true });
  const { width, height } = fitted.info;
  const rounded = await sharp(fitted.data)
    .composite([{ input: roundedRectSvg(width, height, radius), blend: "dest-in" }])
    .png()
    .toBuffer();
  return { data: rounded, width, height };
}

/** A still frame: blurred photo background + the photo itself (or the logo plate on the end card). */
export async function composeSceneStill(
  aspect: MontageAspect,
  image: Uint8Array,
  opts: { endCard: boolean; logo?: Uint8Array },
): Promise<Buffer> {
  const L = layoutFor(aspect);
  const s = BASE_SCALE;
  const W = Math.round(L.w * s);
  const H = Math.round(L.h * s);

  const bg = await blurredBackground(image, W, H, opts.endCard ? 0.38 : 0.55);
  const layers: OverlayOptions[] = [];

  if (opts.endCard) {
    if (opts.logo) {
      const pad = Math.round(L.short * 0.045 * s);
      const maxLogoW = Math.round(L.w * (aspect === "16:9" ? 0.3 : 0.5) * s);
      const maxLogoH = Math.round(L.h * (aspect === "16:9" ? 0.2 : 0.13) * s);
      const logo = await sharp(opts.logo, { limitInputPixels: MAX_INPUT_PIXELS })
        .rotate()
        .resize({ width: maxLogoW, height: maxLogoH, fit: "inside" })
        .png()
        .toBuffer({ resolveWithObject: true });
      const plateW = logo.info.width + pad * 2;
      const plateH = logo.info.height + pad * 2;
      const plate = await sharp({ create: { width: plateW, height: plateH, channels: 4, background: { r: 255, g: 255, b: 255, alpha: 0.96 } } })
        .composite([
          { input: logo.data, left: pad, top: pad },
          { input: roundedRectSvg(plateW, plateH, Math.round(pad * 0.9)), blend: "dest-in" },
        ])
        .png()
        .toBuffer();
      layers.push({
        input: plate,
        left: Math.round(W / 2 - plateW / 2),
        top: Math.round(H * (aspect === "16:9" ? 0.3 : 0.32) - plateH / 2),
      });
    }
  } else {
    const radius = Math.round(L.short * 0.035 * s);
    const photo = await roundedPhoto(image, L.photo.maxW * s, L.photo.maxH * s, radius);
    const left = Math.round(L.photo.cx * s - photo.width / 2);
    const top = Math.round(L.photo.cy * s - photo.height / 2);
    const shadowSvg = Buffer.from(
      `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}"><defs><filter id="b" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="${Math.round(L.short * 0.022 * s)}"/></filter></defs>` +
        `<rect x="${left}" y="${top + Math.round(L.short * 0.022 * s)}" width="${photo.width}" height="${photo.height}" rx="${radius}" fill="#000" fill-opacity="0.6" filter="url(#b)"/></svg>`,
    );
    layers.push({ input: shadowSvg }, { input: photo.data, left, top });
  }

  // JPEG (no alpha needed): far quicker to write and for ffmpeg to decode than PNG.
  return sharp(bg).composite(layers).jpeg({ quality: 93, chromaSubsampling: "4:4:4" }).toBuffer();
}

/**
 * Transparent overlay (canvas size, not upscaled): bottom gradient for legibility
 * plus the scene's text as vector outlines. Returns null when there is nothing to draw.
 */
export async function composeCaptionLayer(aspect: MontageAspect, texts: SceneText[]): Promise<Buffer | null> {
  const L = layoutFor(aspect);
  const [bold, semi, medium] = await Promise.all([loadFont("Bold"), loadFont("SemiBold"), loadFont("Medium")]);

  const styles = {
    title: { font: bold, size: L.short * 0.086, maxLines: 2, fill: "#ffffff" },
    body: { font: medium, size: L.short * 0.057, maxLines: 3, fill: "#f2f2f7" },
    cta: { font: semi, size: L.short * 0.066, maxLines: 2, fill: "#ffffff" },
  } as const;

  interface Item { lines: string[]; style: SceneText["style"]; lineHeight: number; size: number; height: number }
  const items: Item[] = [];
  for (const t of texts) {
    const st = styles[t.style];
    const clean = sanitizeForFont(st.font, t.text);
    if (!clean) continue;
    const lines = wrapText(st.font, clean, st.size, L.textMaxW, st.maxLines);
    const lineHeight = st.size * 1.16;
    items.push({ lines, style: t.style, lineHeight, size: st.size, height: (lines.length - 1) * lineHeight + st.size });
  }
  if (items.length === 0) return null;

  const gap = L.short * 0.03;
  const total = items.reduce((sum, it) => sum + it.height, 0) + gap * (items.length - 1);
  let cursor = L.h - L.textBottom - total;

  const gradientTop = Math.max(0, cursor - L.short * 0.22);
  const parts: string[] = [
    `<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.82"/></linearGradient></defs>`,
    `<rect x="0" y="${gradientTop}" width="${L.w}" height="${L.h - gradientTop}" fill="url(#g)"/>`,
  ];

  for (const it of items) {
    const st = styles[it.style];
    const baseline = cursor + it.size * 0.8;
    if (it.style === "cta" && it.lines.length === 1) {
      const tw = st.font.getAdvanceWidth(it.lines[0]!, it.size, { kerning: true });
      const padX = it.size * 0.7;
      const padY = it.size * 0.42;
      const pw = tw + padX * 2;
      const ph = it.size + padY * 2;
      const px = L.w / 2 - pw / 2;
      const py = cursor - padY * 0.9;
      parts.push(`<rect x="${px.toFixed(1)}" y="${py.toFixed(1)}" width="${pw.toFixed(1)}" height="${ph.toFixed(1)}" rx="${(ph / 2).toFixed(1)}" fill="#ffffff"/>`);
      parts.push(typesetCentered(st.font, it.lines, { centerX: L.w / 2, baselineTop: baseline, fontSize: it.size, lineHeight: it.lineHeight, fill: "#0b0b10" }).svg);
      cursor += ph * 0.9 + gap * 0.4;
      continue;
    }
    parts.push(typesetCentered(st.font, it.lines, { centerX: L.w / 2, baselineTop: baseline, fontSize: it.size, lineHeight: it.lineHeight, fill: st.fill }).svg);
    cursor += it.height + gap;
  }

  const svg = Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${L.w}" height="${L.h}">${parts.join("")}</svg>`);
  return sharp(svg).png().toBuffer();
}
