# Does the fit judge stay trustworthy at scale? Pre-registration

Written 2026-11-08, before any judge output exists.

## Question
At 109 assets the judge flagged 16/18 duplicates with 0/22 false alarms. With ~9,600
assets the top 5 fill with look-alikes. Does the judge start saying "use it" to the
WRONG asset, the most damaging failure the product can have?

## Setup
- Tickets: all 30 duplicate and 26 control tickets in experiments/gate2/tasks*.json.
- Corpus sizes: 109 and 9,610 (the curve corpus: everyone has a description-only purpose
  line from blind writers; experiments/scale/purpose_lines + curve_lines), built inside
  a rolled-back transaction on homegrown_bench, searched as the gate-2 dev user under RLS.
- Retrieval: production search + query rewriting (rewrites: blind Claude, request only;
  experiments/scale/rewrites_claude.json plus the 26 control rewrites added now).
- Judge input: the top 5 per ticket at each size, as production builds it (name,
  description, purpose line, up to 6 non-sensitive claims), with candidate letters.
- Judge: fresh blind Claude instances with the product's exact judge instructions
  (search._JUDGE_SYSTEM), one per corpus size. Production uses Groq gpt-oss-120b, whose
  daily cap can't cover ~112 calls; this is a stated difference.

## Metrics (fixed now)
For each size:
1. duplicates caught: gold judged use_as_is/extend (of 30; and of those whose gold was
   in the top 5)
2. **wrong-yes on duplicates**: a non-gold candidate judged use_as_is/extend, split into
   sibling (same repo as gold) and unrelated
3. **false alarms on controls**: any candidate judged use_as_is/extend (of 26)
4. answer word as the product would show it (yes/no/unknown) per ticket

No change to the judge is made on the basis of these numbers within this run.
