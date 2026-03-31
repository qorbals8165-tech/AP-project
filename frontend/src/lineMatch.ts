/** Normalize for Korean/Latin fuzzy line vs transcript comparison. */
export function normalizeForMatch(s: string): string {
  return s
    .toLowerCase()
    .replace(/\s+/g, "")
    .replace(/[.,!?;:·"'""''()[\]{}]/g, "");
}

export function buildLineRanges(script: string): { start: number; end: number; text: string }[] {
  const parts = script.split(/\r?\n/);
  let offset = 0;
  return parts.map((text) => {
    const start = offset;
    const end = offset + text.length;
    offset = end + 1;
    return { start, end, text };
  });
}

export function lineIndexForCharIndex(
  ranges: { start: number; end: number }[],
  idx: number
): number {
  if (!ranges.length) {
    return 0;
  }
  for (let i = 0; i < ranges.length; i++) {
    if (idx >= ranges[i].start && idx < ranges[i].end) {
      return i;
    }
  }
  for (let i = 0; i < ranges.length - 1; i++) {
    if (idx === ranges[i].end) {
      return i + 1;
    }
  }
  return ranges.length - 1;
}

export function nextNonEmptyLineIndex(lines: string[], from: number): number {
  for (let i = from + 1; i < lines.length; i++) {
    if (lines[i].trim().length > 0) {
      return i;
    }
  }
  return lines.length;
}

export function firstNonEmptyLineIndex(lines: string[]): number {
  return firstNonEmptyFrom(lines, 0);
}

/** First index >= `from` with non-empty trimmed text, or lines.length if none. */
export function firstNonEmptyFrom(lines: string[], from: number): number {
  for (let i = Math.max(0, from); i < lines.length; i++) {
    if (lines[i].trim().length > 0) {
      return i;
    }
  }
  return lines.length;
}

/**
 * Returns true if the spoken transcript likely corresponds to reading `lineText`.
 */
export function lineMatchesSpoken(lineText: string, spokenTranscript: string): boolean {
  const line = normalizeForMatch(lineText);
  const spoken = normalizeForMatch(spokenTranscript);
  if (!line.length || !spoken.length) {
    return false;
  }

  if (spoken.includes(line)) {
    return true;
  }

  if (line.includes(spoken) && spoken.length / line.length >= 0.45) {
    return true;
  }

  let matched = 0;
  let j = 0;
  for (let i = 0; i < line.length && j < spoken.length; i++) {
    while (j < spoken.length && line[i] !== spoken[j]) {
      j++;
    }
    if (j < spoken.length && line[i] === spoken[j]) {
      matched++;
      j++;
    }
  }

  const ratio = matched / line.length;
  const shortLine = line.length <= 8;
  return ratio >= (shortLine ? 0.85 : 0.62);
}
