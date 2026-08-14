# L1 Main Pipeline

Stable mainline run:

```bash
/opt/conda/envs/Globemind_env/bin/python scripts/run_news_l1_main_coref.py
```

Mainline result run id:

```text
fast_l1_v2
```

Output tables:

```text
public.event_coref_clusters
public.event_coref_members
```

Current mainline characteristics:

- No full-text BGE embedding.
- Candidate buckets use `event_family`, `event_action`, actor/location keys, and actor aliases.
- Similarity uses title plus short body context.
- Exact-title union is allowed only for non-generic, non-template titles.
- Weak titles, recurring war reports, source templates, and institutional templates are blocked.
- Long-span over-merge protection is applied to all cluster sizes.

The previous experimental path has been promoted to the main entrypoint:

```bash
/opt/conda/envs/Globemind_env/bin/python scripts/run_news_l1_fast_coref_experimental.py
```

Current official run id:

```text
fast_l1_v2
```

Do not tune `scripts/run_news_l1_fast_coref.py` directly for experiments unless the mainline is intentionally being replaced again.
