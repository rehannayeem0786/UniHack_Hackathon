/** Presentation helpers. Kept out of components so formatting stays consistent. */

export const percent = (value: number | undefined, digits = 0) =>
  value === undefined || Number.isNaN(value)
    ? "—"
    : `${(value * 100).toFixed(digits)}%`;

export const seconds = (value: number | undefined) =>
  value === undefined ? "—" : value < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(1)} s`;

export const integer = (value: number | undefined) =>
  value === undefined ? "—" : value.toLocaleString("en-US");

/** Human-readable field label: MANUFACTURER_NAME -> Manufacturer name. */
export function fieldLabel(field: string) {
  const cleaned = field
    .replace(/_/g, " ")
    .replace(/\bDESC\b/i, "description")
    .replace(/\bDESC1\b/i, "description")
    .toLowerCase()
    .trim();
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

/** Turn a snake_case metric key into a sentence: invoice_all_caps -> Invoice all caps. */
export const humanKey = (key: string) => {
  const spaced = key.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
};

/**
 * Confidence bands. A number between 0 and 1 means nothing to a reader on its
 * own, so it is always shown with the band that decides whether a human looks
 * at the row.
 */
export type Band = "high" | "medium" | "low";

export function band(confidence: number | undefined): Band {
  if (confidence === undefined) return "low";
  if (confidence >= 0.85) return "high";
  if (confidence >= 0.65) return "medium";
  return "low";
}

export const bandVariant: Record<Band, "success" | "warning" | "destructive"> = {
  high: "success",
  medium: "warning",
  low: "destructive",
};

export const bandLabel: Record<Band, string> = {
  high: "High confidence",
  medium: "Review suggested",
  low: "Needs review",
};

/** Word-level diff, used to show how a generated description differs from truth. */
export function wordDiff(got: string, want: string) {
  const a = got.split(/\s+/).filter(Boolean);
  const b = new Set(want.split(/\s+/).filter(Boolean).map((w) => w.toLowerCase()));
  return a.map((word) => ({
    word,
    matched: b.has(word.toLowerCase()),
  }));
}
