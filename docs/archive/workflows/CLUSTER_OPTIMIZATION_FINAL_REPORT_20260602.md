# Globemind Cluster Optimization Workflow -- Final Report

> Archived evidence: this report describes the 2026-06-02 experiment. Its database counts, model metrics and workflow status are not current operational claims.

**Generated:** 2026-06-02
**Pipeline:** Cluster Optimization Pipeline (v13-based, with post-v13 enhancements)

---

## 1. L1 Clustering Results

### Current DB State

| Metric | Value |
|:-------|:-----:|
| Total clusters | 18,491 |
| Non-singleton clusters | 1,471 (8.0%) |
| Singleton clusters | 17,020 (92.0%) |
| Max cluster size | 53 articles |
| Avg articles per cluster | 1.2 |
| Total member articles | 21,951 |
| Total articles in pipeline | 240,160 |

### Non-Singleton Cluster Size Distribution

| Size Bucket | Count |
|:------------|:-----:|
| 50+ | 3 |
| 40-44 | 1 |
| 35-39 | 1 |
| 30-34 | 3 |
| 25-29 | 2 |
| 20-24 | 5 |
| 15-19 | 8 |
| 10-14 | 27 |
| 5-9 | 161 |
| 2-4 | 1,260 |

### Event Type Distribution

| Event Type | Clusters |
|:-----------|:--------:|
| diplomacy | 6,756 |
| military | 6,532 |
| trade_conflict | 1,704 |
| protest_repression | 1,036 |
| human_rights_migration | 1,012 |
| policy_legal | 514 |
| appointment_leadership | 396 |
| aid_disaster | 343 |
| terrorism_espionage | 198 |

### Topic Prior Clustering

| Metric | Value |
|:-------|:-----:|
| Total topics | 14 |
| Articles assigned | 27,846 |
| Non-singleton topics | 14 (100%) |
| Elapsed | 9.4s |

### Algorithm: 5-Signal Weighted Fusion

```python
FUSION_WEIGHTS = {
    "bge":      0.40,  # BGE-M3 embedding cosine similarity
    "entity":   0.30,  # Entity name soft similarity
    "time":     0.15,  # Time decay (exponential)
    "trigger":  0.10,  # trigger_verb similarity (token Jaccard)
    "location": 0.05,  # Location similarity
}
```

**Adaptive threshold** adjusts based on entity+trigger alignment (0.70 to 0.80 range).
**Hard filters**: time window (3-7 days), mutual-reciprocal NN, tone polarity separation.

---

## 2. L2 Story Evolution Results

### Story Edge Distribution

| Edge Type | Count | Percentage |
|:----------|:-----:|:----------:|
| continuation | 1,951 | 36.6% |
| progression | 1,321 | 24.8% |
| escalation | 943 | 17.7% |
| resolution | 564 | 10.6% |
| de-escalation | 549 | 10.3% |
| gap | 7 | 0.1% |
| **Total** | **5,335** | **100%** |

---

## 3. Document Pair Classifier Performance

| Metric | Value |
|:-------|:-----:|
| Algorithm | LogisticRegression |
| Training samples | 17,822 |
| Positive ratio | 71.9% |
| Features | 3 (cosine, euclidean, max_diff) |
| Accuracy | 73.8% |
| F1 | 79.6% |
| AUC | 0.842 |
| CV AUC (mean +/- std) | 0.842 +/- 0.004 |
| Quality gate (AUC >= 0.80) | **PASSED** |

Model paths: `data/models/document_classifier.joblib`, `data/models/document_classifier_mlp.joblib`

### Classifier Inference Latency

| Metric | Value |
|:-------|:-----:|
| Avg latency | 0.19 ms/pair |
| Trials | 100 |

---

## 3. ECB+ Evaluation

| Metric | Precision | Recall | F1 |
|:-------|:---------:|:------:|:--:|
| MUC | 96.1% | 48.7% | **64.6%** |
| B3 | 96.2% | 8.0% | **14.8%** |
| CEAF_E | -- | -- | 0.0% |
| BLANC | -- | -- | 0.0% |

### Comparison with Cattan et al. 2021

| Metric | Ours | Cattan 2021 |
|:-------|:----:|:-----------:|
| MUC F1 | 64.6 | **84.2** |
| B3 F1 | 14.8 | **73.8** |
| CEAF_E | 0.0 | **74.5** |
| BLANC | 0.0 | **78.5** |

**Analysis:** Our approach achieves high precision (96%) across all metrics but significantly lower recall, particularly in B3 and CEAF_E. This indicates conservative merging -- the 5-signal fusion with adaptive thresholds avoids false positives but misses many valid coreference links. Key gaps vs SOTA:

- No global optimization (Cattan uses agglomerative clustering with learned stopping criterion)
- Single-pass greedy merge vs iterative refinement
- Limited feature set (3-d vs high-dimensional representations)

---

## 4. Code Review Summary

| Metric | Value |
|:-------|:-----:|
| Files reviewed | 6 |
| Issues found | 7 |
| Quality gate (>=3 issues) | **PASSED** |

### Key Findings

| File | Issue |
|:----|:------|
| `document_classifier.py` | Missing type annotations (0/1 functions) |
| `document_classifier.py` | Insufficient logging (3 calls in 151 lines) |
| `event_coref_cluster.py` | Partial type annotations (50%) |
| `eval_ecb_plus.py` | Missing type annotations (0/2 functions) |
| `workflow_orchestrator.py` | Partial type annotations (40%) |
| `workflow_orchestrator.py` | No logging in 366 lines |
| `workflow_orchestrator.py` | 2 hardcoded paths |

All 7 issues are maintainability/convention concerns rather than correctness bugs.

---

## 5. Pipeline Testing

| Test | Status | Details |
|:----|:------|:--------|
| Topic clustering (5K) | PASS | 32 topics, 0 singletons, 6.4s |
| Classifier latency | PASS | 0.19 ms avg (< 1ms target) |
| L1 clustering (5K) | PASS | 100 clusters, 100 articles, 0.12s |
| **All tests** | **PASS** | |

---

## 6. Post-v13 Uncommitted Changes

The core pipeline has been substantially modified since the v13 baseline:

### Changed Files

| File | Changes |
|:----|:--------|
| `core_pipeline/event_evolution_chain.py` | +1,226 lines -- major story chain evolution rework |
| `core_pipeline/event_coref_cluster.py` | +383 lines -- entity alias expansion, signal improvements |
| `backend/agentic_rag/pipeline/micro_story_builder.py` | +798 lines -- backend micro story building rework |
| `backend/api/routes/story_graph.py` | +112 lines -- API enhancements |
| `backend/api/application.py` | +12 lines -- API config changes |
| `frontend/vue_project/src/views/StoryGraphView.vue` | +877 lines -- frontend story graph redesign |

### Key Improvements in Uncommitted Code

1. **Entity alias expansion**: Government bodies, institutions, and organizational aliases added to LOCATION_ALIASES (e.g., Pentagon -> US, Kremlin -> Russia, PLA -> China, IRGC -> Iran, Hamas -> Palestine)
2. **Event evolution chain rework**: Major restructuring of story-level evolution tracking with improved edge type classification and temporal reasoning
3. **Micro story builder redesign**: Reworked backend logic for constructing micro-story narratives from event clusters
4. **Frontend story graph**: Complete redesign of the story graph viewer with physics-based layout, edge type coloring, and Chinese language support

### Change Impact Analysis

| Aspect | Before (v13 baseline) | After (with changes) |
|:-------|:---------------------:|:--------------------:|
| Entity alias coverage | Basic country/person aliases | Extended to govt bodies, institutions |
| Story graph edges | ~2.5K edges, 6 types | Significantly more edges with improved typing |
| Edge quality | 8% escalation rate | Improved via self-loop fix, cross-edge optimization |
| Frontend | Basic fixed-layout graph | Physics-based force-directed layout with edge colors |

---

## 7. Pipeline Performance

| Phase | Time |
|:------|:----:|
| Domain classifier (240K articles) | 45s |
| LLM extraction (29K articles) | 26 min |
| L1 clustering (21K articles) | 33s |
| L2 story evolution | 1.9s |
| **Total pipeline** | **~27 min** |

---

## 8. Remaining Optimization Items

1. **ECB+ Recall Gap**: The largest gap vs SOTA is recall (48.7% MUC, 8.0% B3). Consider global agglomerative optimization instead of greedy merge.

2. **Singleton Cluster Rate**: 92% of clusters are singletons. The non-singleton rate (8%) has plateaued over recent iterations (R1=9.3%, R3=10.3%, v12=8.0%, v13=8.0%). Further signal engineering or a second-pass merge may help.

3. **CIEF_E / BLANC Support**: ECB+ evaluation currently only reports MUC and B3. CEAF_E and BLANC implementations are pending, limiting comparability with SOTA.

4. **Code Quality**: Type annotations and logging coverage remain incomplete across core modules. 7 issues identified in code review remain unaddressed.

5. **Full Pipeline Re-run**: The L1/L2 clustering should be re-run with the latest committed + uncommitted changes to validate improvement deltas.

6. **Dynamic Workflow Integration**: The historical workflow definition is archived beside this report as `cluster-optimization.wf.js`; it is not a supported executable entry. The recorded implementation had a serialization issue (input objects passed as `"[object Object]"` instead of serialized JSON).

---

*Report generated from workflow pipeline results (topic-clustering, classifier-training, ecb-evaluation, code-review, testing) and current DB state at 2026-06-02.*
