# L1 Coreference Recall Experiment - 2026-06-27

## Goal

Improve L1 event coreference recall without materially reducing precision.

## Runs

- Baseline: `fast_l1_eval_base`
- Experiment: `fast_l1_exp`
- Evaluation mode: common `news_id` intersection only, `278,509` rows
- Promotion result: the experimental logic was promoted to official mainline `fast_l1_v2`

## Method

- Keep the stable mainline script unchanged.
- Use alias-aware actor buckets in the experimental script:
  - leader-to-country aliases such as `Trump -> us`, `Putin -> russia`, `Xi -> china`
  - symmetric buckets for `meeting_visit`, `negotiation_talks`, `agreement_signed`, `ceasefire_peace_talks`
- Add conservative exact-title union for non-generic duplicates.
- Block exact-title union for template-like titles where the same title maps to dispersed `event_family/event_action/entity` fields and has no event action signal.

## Metrics

Filtered silver positives are exact duplicate titles after excluding template-like title groups. Precision is tracked with proxy risks: generic/roundup member rate, market-noise member rate, low-title-similarity pair rate, mixed-field clusters, and template-title clusters.

| Metric | Baseline | Experiment | Delta |
| --- | ---: | ---: | ---: |
| Non-singleton members | 15,461 | 18,470 | +3,009 |
| Filtered silver pair recall | 0.408899 | 0.811179 | +0.402280 |
| Filtered silver best-cluster member recall | 0.694220 | 0.941939 | +0.247719 |
| Filtered silver split rows | 4,608 | 796 | -3,812 |
| Generic/roundup member rate | 0.006856 | 0.001083 | -0.005773 |
| Market-noise member rate | 0.010349 | 0.009908 | -0.000441 |
| Low-title-similarity pair rate | 0.036166 | 0.032806 | -0.003361 |
| Low-title-similarity clusters | 0 | 0 | 0 |
| Mixed-field clusters | 0 | 0 | 0 |
| Template-title clusters | 1 | 0 | -1 |
| Max cluster size | 52 | 64 | +12 |

## Conclusion

The experimental path improves the measurable recall baseline substantially while precision-risk proxies do not increase. Manual audit found and fixed weak-title, recurring-series, source-template, institutional-template, pure-generic-title, and long-span pair over-merges. The final logic has been promoted to `fast_l1_v2`; its final run has no non-singleton cluster spanning more than 3 days. The main remaining risk is over-large geopolitical story clusters that include background, analysis, and market/context articles; this appears acceptable for L1 routing but not for strict atomic event coreference.
