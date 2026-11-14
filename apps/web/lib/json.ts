// Strict JSON for bundle files. It refuses what the Python bundle validator refuses (PRD 8.4):
// bytes that are not UTF-8, a byte order mark, NaN or Infinity (JSON.parse never accepts them)
// and duplicate keys, which JSON.parse would silently resolve to the last value.
import { BundleError } from './errors.ts';

// fatal: invalid UTF-8 throws instead of becoming U+FFFD. ignoreBOM: a BOM stays in the text, so
// JSON.parse refuses it, as Python's json.loads does.
const UTF8 = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

function isJsonWhitespace(char: string): boolean {
  return char === ' ' || char === '\t' || char === '\n' || char === '\r';
}

/** The first key that appears twice in one object of valid JSON text, or null. */
function duplicateKey(text: string): string | null {
  const objects: (Set<string> | null)[] = [];
  let i = 0;
  while (i < text.length) {
    const char = text.charAt(i);
    if (char === '"') {
      let end = i + 1;
      while (text.charAt(end) !== '"') end += text.charAt(end) === '\\' ? 2 : 1;
      const literal = text.slice(i, end + 1);
      i = end + 1;
      while (isJsonWhitespace(text.charAt(i))) i += 1;
      const keys = objects.at(-1);
      if (text.charAt(i) === ':' && keys) {
        const key = JSON.parse(literal) as string;
        if (keys.has(key)) return key;
        keys.add(key);
      }
      continue;
    }
    if (char === '{') objects.push(new Set());
    else if (char === '[') objects.push(null);
    else if (char === '}' || char === ']') objects.pop();
    i += 1;
  }
  return null;
}

/** Decodes and parses one bundle file. `label` names the file in error messages. */
export function decodeJson(bytes: Uint8Array, label: string): unknown {
  let text: string;
  try {
    text = UTF8.decode(bytes);
  } catch {
    throw new BundleError(`${label} is not valid UTF-8`);
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    throw new BundleError(`${label} is not valid JSON: ${reason}`);
  }
  const key = duplicateKey(text);
  if (key !== null) {
    throw new BundleError(`${label} has a duplicate key ${JSON.stringify(key)}`);
  }
  return value;
}
