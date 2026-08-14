# Adaptive Benchmark Report

- Input: [discovered_urls_wave1_20sites_capped50.jsonl](data/historical_news/discovered_urls_wave1_20sites_capped50.jsonl)
- Created at: `2026-06-20T21:42:43.879220+00:00`

## Results

- `global=8` `per_domain=4` `success=332/360` `rate=3.07/s` `min_rate=184.2/min`
- `global=16` `per_domain=4` `success=332/360` `rate=2.936/s` `min_rate=176.2/min`
- `global=32` `per_domain=4` `success=333/360` `rate=2.722/s` `min_rate=163.3/min`
- `global=64` `per_domain=4` `success=333/360` `rate=2.73/s` `min_rate=163.8/min`

## Best Setting

- `global_concurrency=8`
- `max_per_domain=4`
- Throughput: `184.2 articles/min`
- Success rate: `0.9222`
