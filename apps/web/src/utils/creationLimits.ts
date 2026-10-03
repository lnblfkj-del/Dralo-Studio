// These capacities mirror app/core/creation_limits.py; model budgets are separate.
export const MAX_EPISODES = 300;
export const MAX_STORY_CHARACTERS = 100;
export const MAX_SOURCE_CHARACTERS = 3_000_000;
export const MAX_REFERENCE_BYTES = 20 * 1024 * 1024;

export function textCharacterCount(text: string): number {
  let count = 0;
  for (const character of text) count += Math.min(character.length, 1);
  return count;
}

let indexedText = "";
let offsets: Uint32Array | null = null;
export function characterSlice(text: string, start: number, end?: number): string {
  if (indexedText !== text) {
    indexedText = text;
    offsets = null;
    if (/[\uD800-\uDBFF][\uDC00-\uDFFF]/.test(text)) {
      const map = new Uint32Array(textCharacterCount(text) + 1);
      let index = 0, utf16 = 0;
      for (const character of text) { map[index++] = utf16; utf16 += character.length; }
      map[index] = utf16;
      offsets = map;
    }
  }
  if (!offsets) return text.slice(start, end);
  return text.slice(offsets[Math.min(start, offsets.length - 1)], end === undefined ? undefined : offsets[Math.min(end, offsets.length - 1)]);
}
