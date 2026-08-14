# Legacy Endpoint Retirement

V0.10 retires nine read-only opinion endpoints whose source relations are not
part of the supported Web runtime schema. Repository frontend and backend call
sites were audited before retirement; only legacy tests referenced these paths.

The GET paths remain mounted and always return HTTP 410 without resolving a
database session:

- `/api/opinion/micro-story-sub-events`
- `/api/opinion/event-timeseries`
- `/api/opinion/global-attention`
- `/api/opinion/sentiment-polarity`
- `/api/opinion/influence-index`
- `/api/opinion/composite-index`
- `/api/opinion/topic-breakdown`
- `/api/opinion/frame-breakdown`
- `/api/opinion/narrative-dispersion`

The response contract is stable:

```json
{
  "ok": false,
  "code": "endpoint_retired",
  "status": 410,
  "endpoint": "/api/opinion/global-attention",
  "message": "This legacy opinion endpoint was retired because its data source is no longer part of the supported runtime schema. Alternatives are not drop-in replacements.",
  "retired_in": "v0.10",
  "alternatives": ["/api/opinion/overview"]
}
```

`alternatives` lists supported analyst workflows, not response-compatible
replacements. Unknown external clients can therefore distinguish permanent
retirement from an empty result or a transient database failure.

## Graph briefing migration

`/api/graph/*` remains active and now reads only the current hierarchy:
`event_l3_macro_events` and members, `event_l2_chains` and segments,
`event_coref_members`, and `news`. Macro and chain identifiers are opaque text;
legacy numeric request IDs remain accepted and are normalized to text. The
assistant bridge uses the current text IDs, and the smoke script exercises all
nine paths. No legacy graph relation remains in the Web runtime policy.

| Method and path | Repository consumer evidence |
|---|---|
| `GET /api/graph/macros/search` | Runtime assistant bridge and graph smoke script |
| `POST /api/graph/micros/news-batch` | Runtime assistant bridge and graph smoke script |
| `GET /api/graph/universe` | Graph smoke script |
| `GET /api/graph/macro/{storyline_id}` | Runtime assistant bridge and graph smoke script |
| `GET /api/graph/macro/{storyline_id}/briefing` | Graph smoke script |
| `GET /api/graph/macro/{storyline_id}/micros` | Runtime assistant bridge and graph smoke script |
| `GET /api/graph/macro/{storyline_id}/tree` | Graph smoke script |
| `GET /api/graph/micro/{event_id}` | Runtime assistant bridge and graph smoke script |
| `GET /api/graph/micro/{event_id}/news` | Graph smoke script |
