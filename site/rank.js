// Corpus frequency ranks + verse difficulty. Mirrors parser.py
// corpus_ranks / verse_difficulty exactly — see PHASE12_DESIGN.md D2.
// Parity is tested (test_static_export.py); change both sides together.
export function corpusRanks(tokenLists) {
  const counts = new Map();
  for (const toks of tokenLists)
    for (const t of toks) counts.set(t, (counts.get(t) || 0) + 1);
  const ordered = [...counts.entries()].sort(
    (x, y) => y[1] - x[1] || (x[0] < y[0] ? -1 : 1));
  const ranks = new Map();
  ordered.forEach(([form], i) => ranks.set(form, i + 1));
  return ranks;
}

export function verseDifficulty(forms, ranks, target = 0.95, known = new Set()) {
  if (!forms.length) return null;
  const fallback = ranks.size + 1;
  const eff = forms
    .map(f => (known.has(f) ? 0 : (ranks.get(f) ?? fallback)))
    .sort((a, b) => a - b);
  const k = Math.max(1, Math.ceil(target * eff.length));
  return eff[k - 1];
}
