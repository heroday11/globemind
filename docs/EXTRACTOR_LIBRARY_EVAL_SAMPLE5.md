# Extractor Library Eval Sample 5

Test set:

- `5` real article pages
- Languages: `en`, `es`, `ja`
- Sources: `AP News`, `Asahi`

Output file:

- [extractor_comparison_sample5.jsonl](/root/data/globemind/data/historical_news/extractor_comparison_sample5.jsonl)

## Summary

Field hit counts over `5` pages:

- `custom`: title `5/5`, publish time `5/5`, language `5/5`, author `0/5`, long body `5/5`
- `trafilatura`: title `5/5`, publish time `5/5`, language `0/5`, author `3/5`, long body `4/5`
- `goose3`: title `5/5`, publish time `5/5`, language `5/5`, author `4/5`, long body `3/5`
- `newspaper3k`: title `5/5`, publish time `5/5`, language `5/5`, author `1/5`, long body `2/5`

## Practical Reading

- `custom`:
  - Best body stability in this sample
  - Good title and timestamp extraction
  - Missing author extraction
- `trafilatura`:
  - Best overall metadata helper in this sample
  - Often gets author
  - Body quality is usually good, but date granularity is sometimes only day-level
  - Language was not filled in this sample
- `goose3`:
  - Strong metadata extraction on AP pages
  - Weak body extraction on Asahi pages in this sample
  - Sometimes keeps page chrome on AP
- `newspaper3k`:
  - Title and date are usable
  - Body quality is the weakest here
  - Sometimes extracts biography/footer noise as author or content

## Recommendation

For this project, the best current direction is:

1. Keep the current custom extractor as the main body extractor.
2. Add `trafilatura` as a metadata enhancement layer, especially for `author`, `date`, and fallback text.
3. Keep `goose3` only as a secondary fallback for author/date on selected English sites.
4. Do not use `newspaper3k` as the main extractor for this corpus.

## Note

Installing these libraries upgraded `lxml` in the project environment from `5.4.0` to `6.1.1`.
`crawl4ai` currently declares `lxml~=5.3`, so if that part of the repo is used later, it should be rechecked.
