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

export function nextWords(tokenLists, ranks, N, known, target = 0.95, topN = 10) {
  const fallback = ranks.size + 1;
  const unlocks = new Map();
  for (const toks of tokenLists) {
    if (!toks.length) continue;
    const slack = toks.length - Math.max(1, Math.ceil(target * toks.length));
    const over = new Map(); // stem -> occurrences with eff rank > N
    let overTotal = 0;
    for (const t of toks) {
      const r = known.has(t) ? 0 : (ranks.get(t) ?? fallback);
      if (r > N) { over.set(t, (over.get(t) || 0) + 1); overTotal++; }
    }
    if (overTotal <= slack) continue;            // already readable
    for (const [stem, c] of over)
      if (overTotal - c <= slack)                // learning stem unlocks it
        unlocks.set(stem, (unlocks.get(stem) || 0) + 1);
  }
  return [...unlocks.entries()]
    .map(([stem, count]) => ({ stem, count, rank: ranks.get(stem) ?? fallback }))
    .sort((a, b) => b.count - a.count || a.rank - b.rank)
    .slice(0, topN);
}
