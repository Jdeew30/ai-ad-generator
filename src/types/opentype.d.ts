// Minimal typings for the few opentype.js APIs we use (the package ships none).
declare module "opentype.js" {
  export interface Path {
    toPathData(decimalPlaces?: number): string;
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
