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

/** Order-preserving greedy subsequence match ratio (0..1). */
function subsequenceRatio(line: string, spoken: string): number {
  if (!line.length) return 0;
  let matched = 0;
  let j = 0;
  for (let i = 0; i < line.length && j < spoken.length; i++) {
    while (j < spoken.length && line[i] !== spoken[j]) j++;
    if (j < spoken.length && line[i] === spoken[j]) {
      matched++;
      j++;
    }
  }
  return matched / line.length;
}

/**
 * Returns true if the spoken transcript likely corresponds to reading `lineText`.
 *
 * 어절(단어) 단위 겹침을 기본으로 사용한다. 그리디 부분문자열 비교는 한 단어만
 * 어긋나도(예: 스크립트 "Whisper" vs 발화 "위스퍼") 뒤쪽 전체가 실패하므로
 * 단어 단위가 ASR/표기 차이에 훨씬 강건하다.
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
  if (line.includes(spoken) && spoken.length / line.length >= 0.5) {
    return true;
  }

  // 어절 단위 겹침 (2자 이상 단어만 사용)
  const words = lineText
    .split(/\s+/)
    .map(normalizeForMatch)
    .filter((w) => w.length >= 2);

  if (words.length === 0) {
    // 단어가 거의 없는 매우 짧은 줄은 부분문자열 비율로 판정
    return subsequenceRatio(line, spoken) >= 0.85;
  }

  let hit = 0;
  for (const w of words) {
    if (spoken.includes(w)) hit++;
  }
  const ratio = hit / words.length;
  const shortLine = words.length <= 2;
  return ratio >= (shortLine ? 0.85 : 0.5);
}
