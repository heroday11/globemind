# L1 Coreference Manual Audit - 2026-06-27

## Scope

- Run audited: `fast_l1_exp`
- Baseline for A/B: `fast_l1_eval_base`
- Evaluation set: common `news_id` intersection, `278,509` rows
- Promotion result: the audited logic was promoted to official mainline `fast_l1_v2`

## Final Quantitative Result

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

## Manual Spot Check

Random small clusters, medium clusters, large clusters, and known risk clusters were inspected.

Observed good cases:

- `Trump/US -> Putin/Russia` summit clusters are now merged across leader-country aliases.
- `Xi/China -> Kim/North Korea` visit clusters merge across leader-country aliases.
- Small duplicate/newswire variants such as UK spy-service arrests, Iran execution, Ukraine strikes, US-India tariffs, and Apple-Intel chip production cluster correctly.
- Market-context articles tied to a geopolitical event are grouped at story level, for example US-Iran peace deal market reactions.

Known bad cases found and fixed during audit:

- Short titles such as `以色列` were incorrectly linkable.
- `Video ...` military snippets were over-merged.
- Daily recurring war-report titles such as `over past day` and `General Staff: Russia has lost ... troops since ...` were over-merged.
- France Diplomatie / committee-hearing template pages were over-merged.
- Generic NATO institutional meeting titles merged different locations such as Lisbon, Brussels, and Athens.
- Two-member exact-title clusters could span months because the inherited splitter did not split pairs.
- Pure low-information titles such as `Opening Remarks` were linkable inside same-day template clusters.

All listed bad examples were rechecked after the final run and now resolve to singleton clusters.

Final time-span check:

- `span = 0 days`: 4,712 non-singleton clusters
- `span = 1-3 days`: 3,161 non-singleton clusters
- `span >= 4 days`: 0 non-singleton clusters
- max span: 3 days

## Judgment

The audited logic, now published as `fast_l1_v2`, is acceptable for L1 story/routing clustering:

- Recall is materially higher than the saved baseline.
- Precision-risk proxy metrics did not increase.
- Manual samples show clusters are mostly coherent at story level.
- Previously observed template/title failure modes are blocked.

It is not a strict atomic event-coreference solution:

- Large clusters intentionally group background, preview, outcome, analysis, and market-reaction articles around one story.
- For downstream tasks that need atomic event timelines, L1 should be followed by an L1.5/L2 splitter using event_action, date, title angle, and extracted entities.
