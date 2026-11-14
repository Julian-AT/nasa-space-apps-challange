// Strict JSON decoding for bundle files, matching what the Python validator refuses (PRD 8.4):
// bytes that are not UTF-8, a byte order mark, and duplicate keys, which JSON.parse would
// silently resolve to the last value.
import { describe, expect, it } from 'vitest';

import { decodeJson } from '../lib/json.ts';

const bytes = (text: string): Uint8Array => new TextEncoder().encode(text);

describe('decodeJson', () => {
  it('parses JSON text', () => {
    expect(decodeJson(bytes('{"a": [1, {"b": "é"}], "c": null}\n'), 'x.json')).toStrictEqual({
      a: [1, { b: 'é' }],
      c: null,
    });
  });

  it('allows the same key in different objects', () => {
    expect(decodeJson(bytes('[{"a": 1}, {"a": 2, "b": {"a": 3}}]'), 'x.json')).toStrictEqual([
      { a: 1 },
      { a: 2, b: { a: 3 } },
    ]);
  });

  it.each([
    { name: 'at the top level', text: '{"mock": true, "mock": false}' },
    { name: 'in a nested object', text: '{"model": {"g5": "PASS", "id": "x", "g5": "FAIL"}}' },
    { name: 'inside an array', text: '[{"a": 1}, {"b": 1, "b": 2}]' },
    { name: 'written with an escape', text: '{"mock": true, "\\u006dock": false}' },
    { name: 'after a string value holding a brace', text: '{"a": "}{\\"", "a": 1}' },
  ])('refuses a duplicate key $name', ({ text }) => {
    expect(() => decodeJson(bytes(text), 'x.json')).toThrow(/x\.json.*duplicate key/s);
  });

  it.each([
    { name: 'invalid JSON', input: bytes('{"a": 1,') },
    { name: 'NaN', input: bytes('{"a": NaN}') },
    { name: 'empty input', input: bytes('') },
  ])('refuses $name', ({ input }) => {
    expect(() => decodeJson(input, 'x.json')).toThrow(/x\.json/);
  });

  it('refuses bytes that are not UTF-8', () => {
    expect(() => decodeJson(Uint8Array.from([0x22, 0xc3, 0x28, 0x22]), 'x.json')).toThrow(
      /x\.json.*UTF-8/s,
    );
  });

  it('refuses a byte order mark', () => {
    expect(() => decodeJson(bytes('﻿{}'), 'x.json')).toThrow(/x\.json/);
  });
});
