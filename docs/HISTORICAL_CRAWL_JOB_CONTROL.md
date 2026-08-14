# Historical Crawl Job Control

This job layer wraps the current historical crawl flow into a resumable background job:

1. per-site discovery
2. merge discovered URL queues for the current run only
3. resumable global adaptive extraction

## Scripts

- Manager:
  - [historical_crawl_job.py](/root/data/globemind/scripts/historical_crawl_job.py)
- Control CLI:
  - [historical_crawl_ctl.py](/root/data/globemind/scripts/historical_crawl_ctl.py)

## Start In Background

```bash
python3 scripts/historical_crawl_ctl.py start \
  --run-id wave1_1y_v1 \
  --start-date 2025-06-21 \
  --end-date 2026-06-20 \
  --parallel-sites 4 \
  --discovery-workers 2 \
  --global-concurrency 8 \
  --max-per-domain 4 \
  --proxy-pool data/proxy_pool/proxy_pool_manifest_v2.json
```

## Check Progress

```bash
python3 scripts/historical_crawl_ctl.py status --run-id wave1_1y_v1
```

The status view shows:

- overall job status
- current phase
- discovery completed / failed / running / pending site counts
- merged URL count
- extraction processed / remaining / success / fail / rate
- failed discovery sites with log paths
- top extraction error sites when available

## View Logs

Manager log:

```bash
python3 scripts/historical_crawl_ctl.py tail --run-id wave1_1y_v1
```

Single site log:

```bash
python3 scripts/historical_crawl_ctl.py tail --run-id wave1_1y_v1 --site-id bbc_com
```

## Stop Gracefully

```bash
python3 scripts/historical_crawl_ctl.py stop --run-id wave1_1y_v1
```

The manager will:

- mark the job as `stopped`
- stop starting new work
- stop the active child process
- preserve already flushed extraction progress

## Resume From Checkpoint

Use the same `run_id` again:

```bash
python3 scripts/historical_crawl_ctl.py start \
  --run-id wave1_1y_v1 \
  --start-date 2025-06-21 \
  --end-date 2026-06-20 \
  --parallel-sites 4 \
  --discovery-workers 2 \
  --global-concurrency 8 \
  --max-per-domain 4 \
  --proxy-pool data/proxy_pool/proxy_pool_manifest_v2.json
```

Resume behavior:

- completed site discovery is skipped
- completed merge is skipped
- extraction resumes from existing `output + errors` files
- already flushed URLs are not re-fetched

## Output Layout

Job directory:

- `data/historical_news/jobs/<run_id>/state.json`
- `data/historical_news/jobs/<run_id>/heartbeat.json`
- `data/historical_news/jobs/<run_id>/logs/manager.log`
- `data/historical_news/jobs/<run_id>/logs/sites/<site_id>.log`
- `data/historical_news/jobs/<run_id>/wave1_discovered_urls_merged.jsonl`
- `data/historical_news/jobs/<run_id>/wave1_articles_merged.jsonl`
- `data/historical_news/jobs/<run_id>/wave1_articles_merged_errors.jsonl`
- `data/historical_news/jobs/<run_id>/wave1_articles_merged_progress.json`
- `data/historical_news/jobs/<run_id>/wave1_articles_merged_stats.json`

## Notes

- Resume is reliable at the flushed-row level for extraction.
- Discovery errors are tracked per site with separate logs.
- Extraction errors are stored in the merged error JSONL and can be grouped by `site_id`.
