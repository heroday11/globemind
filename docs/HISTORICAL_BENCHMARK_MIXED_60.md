# Historical Benchmark Report

- Input: [discovered_urls_sample.jsonl](/root/data/globemind/data/historical_news/discovered_urls_sample.jsonl)
- Sample size: `60` URLs
- Hardware: `CPU 128`, `RAM 377.3 GiB`, `Free disk 573.4 GiB`

## Results

- `workers=4` `success=60/60` `rate=2.161/s` `min_rate=129.7/min` `download=0.71 MiB/s` `p95=3.615s`
- `workers=8` `success=60/60` `rate=2.739/s` `min_rate=164.3/min` `download=0.9 MiB/s` `p95=4.611s`
- `workers=16` `success=60/60` `rate=2.721/s` `min_rate=163.3/min` `download=0.9 MiB/s` `p95=9.109s`
- `workers=32` `success=60/60` `rate=2.495/s` `min_rate=149.7/min` `download=0.82 MiB/s` `p95=20.455s`

## Best Observed Setting

- Workers: `8`
- Success throughput: `2.739` articles/s
- Success throughput: `164.3` articles/min
- Success rate: `1.0`

## Rough Forecast

- If the large-scale crawl sustains the benchmark rate, `1,000,000` articles would take about `4.2` days (`101.4` hours).
- Real full-run time will be slower than this benchmark because discovery, retries, per-site throttling, and difficult sites add overhead.
