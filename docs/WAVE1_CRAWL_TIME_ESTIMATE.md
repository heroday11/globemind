# Wave 1 Crawl Time Estimate

Window:

- `2023-06-21` to `2026-06-20`
- Total days: `1096`

Wave 1 scope:

- Total sites: `113`
- `media_narrative`: `86`
- `official_direct`: `13`
- `wire_fast`: `14`

## What Was Tested

Throughput benchmark files:

- Single-domain benchmark:
  - [HISTORICAL_BENCHMARK_AP_60.md](/root/data/globemind/docs/HISTORICAL_BENCHMARK_AP_60.md)
- Mixed-domain benchmark:
  - [HISTORICAL_BENCHMARK_MIXED_60.md](/root/data/globemind/docs/HISTORICAL_BENCHMARK_MIXED_60.md)
- Adaptive global-pool benchmark:
  - [ADAPTIVE_BENCHMARK_MIXED_120.md](/root/data/globemind/docs/ADAPTIVE_BENCHMARK_MIXED_120.md)
- Multi-proxy benchmark on 8 domains:
  - [ADAPTIVE_BENCHMARK_PROXY_POOL_240.md](/root/data/globemind/docs/ADAPTIVE_BENCHMARK_PROXY_POOL_240.md)
- Multi-proxy benchmark on 18 domains:
  - [ADAPTIVE_BENCHMARK_PROXY_POOL_WAVE1_18DOMAINS_360.md](/root/data/globemind/docs/ADAPTIVE_BENCHMARK_PROXY_POOL_WAVE1_18DOMAINS_360.md)

Representative single-day volume probes:

- `wire` sample:
  - AP on `2026-06-20`: `678` URLs
  - [AP_WORLD_2026-06-20_DISCOVERY_REPORT.md](/root/data/globemind/docs/AP_WORLD_2026-06-20_DISCOVERY_REPORT.md)
- `media` sample:
  - Asahi on `2026-06-20`: `176` URLs
  - [ASAHI_2026-06-20_DISCOVERY_REPORT.md](/root/data/globemind/docs/ASAHI_2026-06-20_DISCOVERY_REPORT.md)
- `official` sample:
  - ASEAN on `2026-06-20`: `2` URLs
  - [ASEAN_2026-06-20_DISCOVERY_REPORT.md](/root/data/globemind/docs/ASEAN_2026-06-20_DISCOVERY_REPORT.md)

## Benchmark Reading

Single-domain AP test:

- Best at `workers=4`
- Throughput about `49.1` articles/min
- Increasing concurrency beyond `4` did not help
- This means same-domain crawling is bottlenecked by remote response / proxy path, not local CPU

Mixed-domain test:

- Best at `workers=8`
- Throughput about `164.3` articles/min
- `16` is roughly flat
- `32` starts increasing latency with no throughput gain

Adaptive global-pool test:

- Best at `global_concurrency=8`, `max_per_domain=4`
- Throughput about `159.4` articles/min
- `16`, `32`, `64` global concurrency did not produce a meaningful gain
- This means the current bottleneck is upstream site responsiveness / proxy path, not local hardware

Multi-proxy test on the older 8-domain sample:

- Best at `global_concurrency=32`, `max_per_domain=4`
- Throughput about `159.0` articles/min
- This is effectively flat versus the earlier single-proxy adaptive result
- Conclusion: on only `8` active domains, adding more proxy exits alone does not raise throughput

Multi-proxy test on a broader 18-domain Wave 1 sample:

- Before URL cleanup:
  - best at `global_concurrency=8`, `max_per_domain=4`
  - throughput about `184.2` articles/min
  - success rate about `92.2%`
- After adding site-level discovery filters for noisy URLs:
  - `global_concurrency=8`, `max_per_domain=4`
  - throughput about `197.6` to `199.8` articles/min
  - success rate about `99.4%`
- Conclusion:
  - broader domain coverage does help more than raising raw concurrency
  - URL quality filtering is now the highest-leverage optimization

Conclusion:

- The server is not the bottleneck.
- A multi-domain crawl benefits from moderate concurrency.
- Proxy pool rotation helps resilience, but not by itself enough to create a large speedup.
- Discovery quality directly changes extraction throughput and success rate.
- Recommended starting point:
  - discovery phase: `discovery_workers=2`, `parallel_sites=4`
  - extraction phase: adaptive global pool with proxy pool, `global_concurrency=8`, `max_per_domain=4`

## Time Scenarios

Observed one-day sample counts vary a lot by source type, so the total article count must be estimated as a range.

### Scenario Assumptions

Conservative:

- media: `44/day`
- official: `2/day`
- wire: `170/day`

Base:

- media: `88/day`
- official: `4/day`
- wire: `339/day`

Upper sampled:

- media: `176/day`
- official: `2/day`
- wire: `678/day`

### Estimated Total Articles Over 1096 Days

- Conservative: `6,784,240`
- Base: `13,553,136`
- Upper sampled: `27,020,784`

### If Sustained At Mixed-Domain Best Rate

Using `159.4 articles/min`:

- Conservative: about `29.6` days
- Base: about `59.0` days
- Upper sampled: about `117.7` days

### If Performance Falls Back To Single-Domain-Like Rate

Using `49.1 articles/min`:

- Conservative: about `96.0` days
- Base: about `191.7` days
- Upper sampled: about `382.2` days

### If Sustained At Filtered 18-Domain Mixed Rate

Using `197.6 articles/min`:

- Conservative: about `23.8` days
- Base: about `47.6` days
- Upper sampled: about `95.0` days

## Practical Recommendation

The realistic expectation is not the single-domain worst case and not the sampled upper-volume best case.

For the first full production run, a defensible planning estimate is:

- likely article volume: `7M` to `14M`
- likely wall-clock crawl time: about `1` to `2` months

If you prioritize only the most recent one year first, a rough estimate at the adaptive benchmark rate is:

- conservative one-year volume: about `2.26M` articles -> about `9.8` days
- base one-year volume: about `4.51M` articles -> about `19.7` days
- upper sampled one-year volume: about `9.00M` articles -> about `39.2` days

If the production crawl can stay near the filtered 18-domain mixed rate, a more optimistic one-year estimate is:

- conservative one-year volume: about `2.26M` articles -> about `7.9` days
- base one-year volume: about `4.51M` articles -> about `15.8` days
- upper sampled one-year volume: about `9.00M` articles -> about `31.6` days

That means:

- `1 week` for the full three-year Wave 1 corpus is not realistic on the current path
- `1 week` for a strict one-year full Wave 1 corpus is only plausible in the conservative-volume case
- the viable emergency plan is:
  - prioritize one year first
  - or prioritize a smaller high-value subset inside Wave 1
  - then backfill the remaining two years

That estimate assumes:

- Wave 1 sites only
- multi-domain scheduling
- moderate retries
- no major proxy failure
- no heavy per-site anti-bot escalation

## Bulk Runner

Prepared bulk runner:

- [run_wave1_historical_crawl.py](/root/data/globemind/scripts/run_wave1_historical_crawl.py)
- [merge_discovered_url_queues.py](/root/data/globemind/scripts/merge_discovered_url_queues.py)
- [adaptive_global_extractor.py](/root/data/globemind/scripts/adaptive_global_extractor.py)

Recommended production flow:

```bash
python3 scripts/run_wave1_historical_crawl.py \
  --start-date 2023-06-21 \
  --end-date 2026-06-20 \
  --parallel-sites 4 \
  --discovery-workers 2 \
  --skip-extract
```

```bash
python3 scripts/merge_discovered_url_queues.py
```

```bash
.env_torch/bin/python scripts/adaptive_global_extractor.py \
  --input data/historical_news/wave1_discovered_urls_merged.jsonl \
  --output data/historical_news/wave1_articles_merged.jsonl \
  --errors data/historical_news/wave1_articles_merged_errors.jsonl \
  --stats data/historical_news/wave1_articles_merged_stats.json \
  --global-concurrency 8 \
  --max-per-domain 4 \
  --proxy-pool data/proxy_pool/proxy_pool_manifest_v2.json \
  --timeout 20 \
  --shuffle
```
