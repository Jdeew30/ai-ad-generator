import { spawnSync } from "node:child_process";
import sharp from "sharp";
import { resolveFfmpeg } from "@/modules/montage/ffmpeg";

/** Every working ffmpeg we can find: the bundled one (old, 2018) and any system one. */
export function availableFfmpegs(): { name: string; path: string }[] {
  const found: { name: string; path: string }[] = [];
  const bundled = resolveFfmpeg();
  if (bundled) found.push({ name: "resolved", path: bundled });
  const sys = spawnSync("ffmpeg", ["-version"], { stdio: "ignore" });
  if (sys.status === 0 && bundled !== "ffmpeg") found.push({ name: "system", path: "ffmpeg" });
  return found;
}

export interface Probe {
  duration: number;
  width: number;
  height: number;
  codec: string;
  pixFmt: string;
  hasAudio: boolean;
}

/** Reads container facts from `ffmpeg -i` (ffprobe is not bundled). */
export function probe(ffmpeg: string, file: string): Probe {
  const r = spawnSync(ffmpeg, ["-hide_banner", "-i", file], { encoding: "utf8" });
  const text = `${r.stderr}`;
  const dur = /Duration: (\d+):(\d+):(\d+\.\d+)/.exec(text);
  const vid = /Video: (\w+)[^,]*, (\w+)[^,]*, (\d+)x(\d+)/.exec(text);
  if (!dur || !vid) throw new Error(`Cannot probe ${file}:\n${text}`);
  return {
    duration: Number(dur[1]) * 3600 + Number(dur[2]) * 60 + Number(dur[3]),
    codec: vid[1]!,
    pixFmt: vid[2]!,
    width: Number(vid[3]),
    height: Number(vid[4]),
    hasAudio: /Audio:/.test(text),
  };
}

/** Decodes one frame at `seconds` as raw RGB so tests can look at actual pixels. */
export async function frameAt(ffmpeg: string, file: string, seconds: number) {
  const r = spawnSync(
    ffmpeg,
    ["-hide_banner", "-loglevel", "error", "-ss", String(seconds), "-i", file, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
    { maxBuffer: 64 * 1024 * 1024 },
  );
  if (r.status !== 0 || r.stdout.length === 0) throw new Error(`Cannot extract frame at ${seconds}s`);
  const { data, info } = await sharp(r.stdout).raw().toBuffer({ resolveWithObject: true });
  return { data, width: info.width, height: info.height, channels: info.channels };
}

/** Share of pixels in a horizontal band that are near-white (typeset text / pill). */
export function whiteShare(frame: { data: Buffer; width: number; height: number; channels: number }, fromY: number, toY: number): number {
  let white = 0;
  let total = 0;
  for (let y = Math.floor(frame.height * fromY); y < Math.floor(frame.height * toY); y++) {
    for (let x = 0; x < frame.width; x++) {
      const i = (y * frame.width + x) * frame.channels;
      total++;
      if (frame.data[i]! > 225 && frame.data[i + 1]! > 225 && frame.data[i + 2]! > 225) white++;
    }
  }
  return white / total;
}

export async function samplePhoto(color = "#c0561f", w = 900, h = 700) {
  return sharp({ create: { width: w, height: h, channels: 3, background: color } })
    .composite([{ input: Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><circle cx="${w / 2}" cy="${h / 2}" r="${h / 4}" fill="#1b3a5c"/></svg>`) }])
    .jpeg()
    .toBuffer();
}

export async function sampleLogo() {
  return sharp({ create: { width: 360, height: 120, channels: 4, background: { r: 20, g: 20, b: 20, alpha: 1 } } }).png().toBuffer();
}
