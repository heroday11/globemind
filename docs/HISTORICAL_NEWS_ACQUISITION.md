# Historical News Acquisition

Target window: `2023-06-21` to `2026-06-21`

## Bottom Line

There is no single safe channel that will give full 3-year fulltext coverage for all curated sites in one shot.

The fastest compliant path for this project is:

1. Use the curated whitelist as the hard boundary.
2. For each whitelisted site, discover historical article URLs from `robots.txt`, sitemaps, feed endpoints, and archive pages.
3. Fetch only those whitelisted article URLs and extract `title`, `body`, `published_at`, `language`, and `request_url`.
4. Use Wayback only as a backfill layer for dead URLs or weak archive sites.
5. Use GDELT and Media Cloud only as metadata / URL discovery supplements, not as the main fulltext store.

## What Was Implemented

- Site probing manifest:
  - [historical_source_manifest_v1_fast.csv](/root/data/globemind/data/source_curation/historical_source_manifest_v1_fast.csv)
  - [HISTORICAL_SOURCE_MANIFEST_V1_FAST_REPORT.md](/root/data/globemind/docs/HISTORICAL_SOURCE_MANIFEST_V1_FAST_REPORT.md)
- URL discovery:
  - [discover_historical_urls.py](/root/data/globemind/scripts/discover_historical_urls.py)
- Article extraction:
  - [extract_historical_articles.py](/root/data/globemind/scripts/extract_historical_articles.py)
- Network helper:
  - [historical_http.py](/root/data/globemind/scripts/historical_http.py)

## Current Probe Result On 173 Priority Sites

- `108` sites: `direct_sitemap`
- `5` sites: `feed_plus_archive`
- `17` sites: `section_archive_plus_wayback`
- `43` sites: `gdelt_or_wayback_plus_direct_fetch`

Interpretation:

- The first `113` sites can usually be started immediately with direct URL discovery.
- The next `17` can often be recovered with archive pages plus Wayback backfill.
- The last `43` are not impossible, but they are not good first-wave bulk targets if speed matters.

## Validation Runs

Sample discovery on 10 media sites:

- [historical_source_manifest_sample.csv](/root/data/globemind/data/source_curation/historical_source_manifest_sample.csv)
- [discovered_urls_sample.jsonl](/root/data/globemind/data/historical_news/discovered_urls_sample.jsonl)
- `1338` discovered URLs

Representative discovery on `bbc_com`, `nikkei_com`, `reuters_world`, `ap_world`, `state_gov`, `whitehouse_gov`:

- [discovered_urls_representative.jsonl](/root/data/globemind/data/historical_news/discovered_urls_representative.jsonl)
- [HISTORICAL_URL_DISCOVERY_REPRESENTATIVE_REPORT.md](/root/data/globemind/docs/HISTORICAL_URL_DISCOVERY_REPRESENTATIVE_REPORT.md)
- `400` discovered URLs
- `bbc_com`, `nikkei_com`, `reuters_world`, `ap_world` worked directly
- `state_gov`, `whitehouse_gov` need fallback handling

Article extraction validation:

- [extracted_articles_sample.jsonl](/root/data/globemind/data/historical_news/extracted_articles_sample.jsonl)
- [extracted_articles_representative.jsonl](/root/data/globemind/data/historical_news/extracted_articles_representative.jsonl)
- Sample extraction succeeded on tested URLs with `0` extraction errors in the validation batches

## How To Run

Build the site-level acquisition manifest:

```bash
python3 scripts/build_historical_source_manifest.py \
  --workers 20 \
  --timeout 6 \
  --output data/source_curation/historical_source_manifest_v1_fast.csv \
  --report docs/HISTORICAL_SOURCE_MANIFEST_V1_FAST_REPORT.md
```

Discover historical URLs:

```bash
python3 scripts/discover_historical_urls.py \
  --input data/source_curation/historical_source_manifest_v1_fast.csv \
  --output data/historical_news/discovered_urls_3y.jsonl \
  --report docs/HISTORICAL_URL_DISCOVERY_REPORT.md \
  --workers 8 \
  --timeout 12 \
  --max-sitemaps-per-site 80
```

Extract article fields:

```bash
/root/data/globemind/.env_torch/bin/python scripts/extract_historical_articles.py \
  --input data/historical_news/discovered_urls_3y.jsonl \
  --output data/historical_news/extracted_articles_3y.jsonl \
  --errors data/historical_news/extracted_articles_3y_errors.jsonl \
  --workers 12 \
  --timeout 20
```

## Channel Decision

- `GDELT`: use for URL discovery and coverage monitoring, not as the sole historical fulltext source.
- `Media Cloud`: use for title/url/date metadata discovery only; weak for official government sites and not suitable as a fulltext export path.
- `Wayback`: use as a targeted fallback for whitelisted URLs or archive sections.
- `CC-News / Common Crawl`: do not use as the primary source here because it is a mixed WARC corpus rather than a whitelist-isolated site archive.

## Next Practical Step

Start bulk acquisition with the `113` sites that are `direct_sitemap` or `feed_plus_archive`.
Those are the fastest path to building a large, cleaner 3-year corpus before tackling the harder official/government backfill group.
