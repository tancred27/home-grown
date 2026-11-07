# Fit judge at 109 vs ~9,610 assets (pre-registered: PREREG_judge.md)

Inputs, blind outputs, key and scores in `judge_scale/`. Judges: two fresh Claude
instances with the product's exact judge instructions (production uses Groq
gpt-oss-120b; its daily cap couldn't cover the run). Retrieval: production search +
query rewriting; everyone's purpose line description-only (curve corpus).

## Raw scores (scripts/scale_judge_score.py)

| | 109 | 9,610 |
|---|---|---|
| gold in top 5 | 27/30 | 25/30 |
| duplicates caught (gold judged use_as_is/extend) | **27/27 retrievable** | **25/25 retrievable** |
| strong verdict on a sibling in the gold's repo | 16 | 12 |
| strong verdict on a non-gold, other-repo asset | 3 (2 tickets) | 5 (5 tickets) |
| controls with any strong verdict | **0/26** | 3/26 |
| answer words (yes / no / unknown) | 28 / 28 / 0 | 31 / 24 / 1 |

## Reading the flagged cases (my classification, listed for audit)

Controls at 9,610, all real solutions that the bigger corpus added (the control
tickets were written for the 109-asset org):
- q06 "compare two semantic versions" → `hashicorp/go-version`, use_as_is: correct.
- g04 postcode validation → `google/libaddressinput`, extend: reasonable.
- g06 distance calculation → `google/s2-geometry-library-java`, extend: reasonable.

Non-gold strong verdicts on duplicate tickets, all `extend` except one, each with the
gap stated:
- p08 alerting → `prometheus/alertmanager` ("routes notifications but does not itself
  evaluate alert rules"): genuinely partial.
- d07 auth middleware → `apache/casbin` ("permission check; token validation still
  needed"): partial.
- d09 work split → `apache/servicecomb-service-center`: weak partial.
- p05 dashboard lint → `detect-angular-dashboards` (related Grafana tool): partial.
- f02 ticketing integration → Business Forms doc pages: the one questionable call
  (use_as_is at 9,610, extend at 109). At 109 the counter's two "unrelated" pages are
  doc pages of the same product family.

## Conclusion

`use_as_is` stays trustworthy at ~9,600 assets: every retrievable duplicate was caught
and its non-gold "use as-is" calls were correct or near-correct. `extend` gets noisier
with scale (more partially related assets), always with an honest gap. The product
currently maps any `extend` to the answer word "Yes": a candidate change, to be decided,
is "Yes" only for use_as_is and "Related, check these" for extend.

Limits: Claude as judge (not the production model), 30 + 26 tickets, one run.
