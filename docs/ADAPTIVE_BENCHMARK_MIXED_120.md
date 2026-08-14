# Adaptive Benchmark Report

- Input: [discovered_urls_sample.jsonl](/root/data/globemind/data/historical_news/discovered_urls_sample.jsonl)
- Created at: `2026-06-20T20:58:32.815539+00:00`

## Results

- `global=8` `per_domain=2` `success=120/120` `rate=2.572/s` `min_rate=154.3/min`
- `global=8` `per_domain=4` `success=120/120` `rate=2.656/s` `min_rate=159.4/min`
- `global=16` `per_domain=2` `success=120/120` `rate=2.567/s` `min_rate=154.0/min`
- `global=16` `per_domain=4` `success=120/120` `rate=2.547/s` `min_rate=152.8/min`
- `global=32` `per_domain=2` `success=120/120` `rate=2.63/s` `min_rate=157.8/min`
- `global=32` `per_domain=4` `success=120/120` `rate=2.535/s` `min_rate=152.1/min`
- `global=64` `per_domain=2` `success=120/120` `rate=2.558/s` `min_rate=153.5/min`
- `global=64` `per_domain=4` `success=119/120` `rate=2.553/s` `min_rate=153.2/min`

## Best Setting

- `global_concurrency=8`
- `max_per_domain=4`
- Throughput: `159.4 articles/min`
- Success rate: `1.0`
