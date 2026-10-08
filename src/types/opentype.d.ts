// Minimal typings for the few opentype.js APIs we use (the package ships none).
declare module "opentype.js" {
  export interface PathCommand {
    type: "M" | "L" | "C" | "Q" | "Z";
    x?: number;
    y?: number;
    x1?: number;
    y1?: number;
    x2?: number;
    y2?: number;
  }

  export interface Path {
    readonly commands: PathCommand[];
  }

  export interface Font {
    readonly unitsPerEm: number;
    charToGlyphIndex(char: string): number;
    getAdvanceWidth(text: string, fontSize?: number, options?: { kerning?: boolean }): number;
    getPath(text: string, x: number, y: number, fontSize: number, options?: { kerning?: boolean }): Path;
  }

  const opentype: { parse(buffer: ArrayBuffer): Font };
  export default opentype;
}
