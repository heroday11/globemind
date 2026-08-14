# Adaptive Benchmark Report

- Input: [discovered_urls_sample.jsonl](data/historical_news/discovered_urls_sample.jsonl)
- Created at: `2026-06-20T21:29:29.389503+00:00`

## Results

- `global=8` `per_domain=4` `success=240/240` `rate=2.641/s` `min_rate=158.5/min`
- `global=8` `per_domain=8` `success=240/240` `rate=2.611/s` `min_rate=156.7/min`
- `global=16` `per_domain=4` `success=239/240` `rate=2.503/s` `min_rate=150.2/min`
- `global=16` `per_domain=8` `success=238/240` `rate=2.583/s` `min_rate=155.0/min`
- `global=32` `per_domain=4` `success=238/240` `rate=2.649/s` `min_rate=159.0/min`
- `global=32` `per_domain=8` `success=240/240` `rate=2.581/s` `min_rate=154.8/min`
- `global=64` `per_domain=4` `success=239/240` `rate=2.547/s` `min_rate=152.8/min`
- `global=64` `per_domain=8` `success=240/240` `rate=2.579/s` `min_rate=154.7/min`

## Best Setting

- `global_concurrency=32`
- `max_per_domain=4`
- Throughput: `159.0 articles/min`
- Success rate: `0.9917`
