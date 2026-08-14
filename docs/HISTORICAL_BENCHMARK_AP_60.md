# Historical Benchmark Report

- Input: [ap_world_2026-06-20_urls.jsonl](/root/data/globemind/data/historical_news/ap_world_2026-06-20_urls.jsonl)
- Sample size: `60` URLs
- Hardware: `CPU 128`, `RAM 377.3 GiB`, `Free disk 573.4 GiB`

## Results

- `workers=4` `success=58/60` `rate=0.818/s` `min_rate=49.1/min` `download=0.52 MiB/s` `p95=10.423s`
- `workers=8` `success=58/60` `rate=0.787/s` `min_rate=47.2/min` `download=0.5 MiB/s` `p95=17.498s`
- `workers=16` `success=58/60` `rate=0.753/s` `min_rate=45.2/min` `download=0.48 MiB/s` `p95=31.744s`
- `workers=32` `success=58/60` `rate=0.743/s` `min_rate=44.6/min` `download=0.48 MiB/s` `p95=60.942s`

## Best Observed Setting

- Workers: `4`
- Success throughput: `0.818` articles/s
- Success throughput: `49.1` articles/min
- Success rate: `0.9667`

## Rough Forecast

- If the large-scale crawl sustains the benchmark rate, `1,000,000` articles would take about `14.1` days (`339.6` hours).
- Real full-run time will be slower than this benchmark because discovery, retries, per-site throttling, and difficult sites add overhead.
