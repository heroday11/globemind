# GlobeMind final closeout handoff

更新时间：2026-08-10 07:51 UTC  
适用范围：源码仓库 `/root/data/globemind`；禁止以 release、运行服务或真实数据库作为输入。

## 剩余事项归属

| 分类 | 最短剩余清单 |
| --- | --- |
| AI 还能处理 | 收到下列外部工件后做离线 schema/hash/范围复核、重跑既有门禁并刷新登记证据；当前没有需要继续扩展的新功能或空 schema。 |
| 需要真实数据 | 三国官方原文与许可工件、批准的 pilot/claim plan；同 corpus 的人工裁决 qrels、slice plan 与 baseline/current ranked runs；真实 AI generation/observation/source artifacts。 |
| 需要人工批准或外部环境 | 试点国家、许可、具名 owner/reviewer、回归阈值与语义/事实审阅；隔离候选、浏览器/移动设备/键盘/读屏验收；CI 历史基线、issue integration 和正式发布决定。 |

以下命令都从仓库根目录执行，只读输入并把收据写到 stdout。输入必须位于隔离目录，不能位于 `/root/data/releases/globemind`、仓库或真实服务的数据目录。所有 SHA-256 均由工件提供方先冻结并独立传递。

## 1. 真实国家资料

最小输入：

- `country-primary-document-bundle.json`，以及其中引用的 UTF-8 官方原文和许可工件；
- 经批准且未过期的 `country-primary-document-pilot-plan.json`；
- 不含 statement 正文、只含 statement SHA-256 和条款引用的 `country-primary-document-claim-plan.json`；
- 三个顶层 JSON 的独立 SHA-256；具名且互异的 owner/reviewer、有效期、国家、文种、版本关系和条款 byte anchor。

```bash
export GM_COUNTRY_BUNDLE=/absolute/isolated/country-primary-document-bundle.json
export GM_COUNTRY_PILOT_PLAN=/absolute/isolated/country-primary-document-pilot-plan.json
export GM_COUNTRY_CLAIM_PLAN=/absolute/isolated/country-primary-document-claim-plan.json
export GM_COUNTRY_BUNDLE_SHA="$(sha256sum "$GM_COUNTRY_BUNDLE" | cut -d' ' -f1)"
export GM_COUNTRY_PILOT_SHA="$(sha256sum "$GM_COUNTRY_PILOT_PLAN" | cut -d' ' -f1)"
export GM_COUNTRY_CLAIM_SHA="$(sha256sum "$GM_COUNTRY_CLAIM_PLAN" | cut -d' ' -f1)"
PYTHONPATH=/root/data/globemind/backend PYTHONDONTWRITEBYTECODE=1 \
  /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B - <<'PY'
import os
from datetime import datetime, timezone
from pathlib import Path
from api.features.authoritative_data import (
    evaluate_country_primary_document_claims,
    evaluate_country_primary_document_readiness,
    load_country_primary_document_bundle,
    load_country_primary_document_claim_plan,
    load_country_primary_document_pilot_plan,
)

now = datetime.now(timezone.utc)
bundle = load_country_primary_document_bundle(
    Path(os.environ["GM_COUNTRY_BUNDLE"]),
    expected_sha256=os.environ["GM_COUNTRY_BUNDLE_SHA"],
    evaluated_at=now,
)
pilot = load_country_primary_document_pilot_plan(
    Path(os.environ["GM_COUNTRY_PILOT_PLAN"]),
    expected_sha256=os.environ["GM_COUNTRY_PILOT_SHA"],
    evaluated_at=now,
)
claim_plan = load_country_primary_document_claim_plan(
    Path(os.environ["GM_COUNTRY_CLAIM_PLAN"]),
    expected_sha256=os.environ["GM_COUNTRY_CLAIM_SHA"],
    evaluated_at=now,
)
print(evaluate_country_primary_document_readiness(
    pilot, bundle, evaluated_at=now
).model_dump_json(indent=2))
print(evaluate_country_primary_document_claims(
    claim_plan, bundle, evaluated_at=now
).model_dump_json(indent=2))
PY
```

通过只表示工件和引用结构对账；`facts_published=false`、语义蕴含和来源真值仍未验证。

## 2. qrels 与 baseline/current ranked runs

最小输入：

- qrels dataset，以及其引用的 corpus manifest、UTF-8 annotation guide、adjudication artifact；
- 覆盖全部 query 且未过期的 translated-intent slice plan；
- 同 dataset/corpus/query contract 的 baseline 和 current ranked-run JSON；current 完成时间必须晚于 baseline，两个工件 SHA 必须不同；
- qrels、plan 和两份 ranked run 的独立 SHA-256。真实 corpus bytes 留在获批隔离存储，不交给此评测器。

```bash
export GM_QRELS=/absolute/isolated/search-qrels-dataset.json
export GM_QRELS_PLAN=/absolute/isolated/search-qrels-slice-plan.json
export GM_BASELINE_RUN=/absolute/isolated/baseline-ranked-run.json
export GM_CURRENT_RUN=/absolute/isolated/current-ranked-run.json
export GM_QRELS_SHA="$(sha256sum "$GM_QRELS" | cut -d' ' -f1)"
export GM_QRELS_PLAN_SHA="$(sha256sum "$GM_QRELS_PLAN" | cut -d' ' -f1)"
export GM_BASELINE_RUN_SHA="$(sha256sum "$GM_BASELINE_RUN" | cut -d' ' -f1)"
export GM_CURRENT_RUN_SHA="$(sha256sum "$GM_CURRENT_RUN" | cut -d' ' -f1)"
PYTHONPATH=/root/data/globemind/backend PYTHONDONTWRITEBYTECODE=1 \
  /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B - <<'PY'
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from api.features.search import (
    LoadedSearchQrelsSliceReceipt,
    compare_search_qrels_slice_receipts,
    evaluate_search_qrels_slices,
    load_search_qrels_bundle,
    load_search_qrels_slice_plan,
    load_search_run_observation_artifact,
)

now = datetime.now(timezone.utc)
qrels = load_search_qrels_bundle(
    Path(os.environ["GM_QRELS"]),
    expected_sha256=os.environ["GM_QRELS_SHA"],
    evaluated_at=now,
)
plan = load_search_qrels_slice_plan(
    Path(os.environ["GM_QRELS_PLAN"]),
    expected_sha256=os.environ["GM_QRELS_PLAN_SHA"],
    evaluated_at=now,
)
baseline_run = load_search_run_observation_artifact(
    Path(os.environ["GM_BASELINE_RUN"]),
    expected_sha256=os.environ["GM_BASELINE_RUN_SHA"],
    evaluated_at=now,
)
current_run = load_search_run_observation_artifact(
    Path(os.environ["GM_CURRENT_RUN"]),
    expected_sha256=os.environ["GM_CURRENT_RUN_SHA"],
    evaluated_at=now,
)
baseline = evaluate_search_qrels_slices(
    qrels, baseline_run, plan, evaluated_at=baseline_run.artifact.completed_at
)
current = evaluate_search_qrels_slices(
    qrels, current_run, plan, evaluated_at=current_run.artifact.completed_at
)
def loaded(receipt):
    raw = receipt.model_dump_json().encode()
    return LoadedSearchQrelsSliceReceipt(
        receipt=receipt,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        artifact_bytes=len(raw),
    )
print(compare_search_qrels_slice_receipts(
    loaded(baseline), loaded(current), evaluated_at=now
).model_dump_json(indent=2))
PY
```

输出只含同范围原始 delta；没有获批阈值时 `regression_claim=not_established`，不能据此声称质量提升或退化。

## 3. AI replay 外部证据

最小输入：

- 冻结的真实 generation artifact 及其 SHA-256；
- body-free `globemind.external-structured-claim-observation.v1`，其 `generation_artifact_sha256` 与前项一致；
- observation 同目录内、由相对 locator 引用的精确 source artifacts；
- observation 的独立 SHA-256。不得放入 prompt、token、账号、用户正文或 secret。

```bash
export GM_AI_OBSERVATION=/absolute/isolated/external-structured-claim-observation.json
export GM_AI_OBSERVATION_SHA="$(sha256sum "$GM_AI_OBSERVATION" | cut -d' ' -f1)"
PYTHONPATH=/root/data/globemind/backend PYTHONDONTWRITEBYTECODE=1 \
  /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B - <<'PY'
import os
from datetime import datetime, timezone
from pathlib import Path
from api.features.assistant import verify_external_structured_claim_observation

receipt = verify_external_structured_claim_observation(
    Path(os.environ["GM_AI_OBSERVATION"]),
    expected_sha256=os.environ["GM_AI_OBSERVATION_SHA"],
    evaluated_at=datetime.now(timezone.utc),
)
print(receipt.model_dump_json(indent=2))
PY
```

该命令不执行模型，只复核 observation、source artifact、inventory 和 claim ID 绑定。真实 replay、claim 切分完整性、source truth、事实与语义蕴含仍需外部模型环境和人工 gold/review。

## 4. 候选浏览器验收

最小输入：

- 经批准、已由外部人员启动的隔离候选，仅暴露 literal loopback origin；
- 本地 Chromium 可执行文件；不得使用真实账号、token 或真实 API；
- 一个新的 `/tmp` 证据目录。runner 固定使用内存 API stub，输出 13 页 × 2 视口、26 张 PNG 和 `browser-smoke.json`。

经批准后才执行浏览器；本次收尾未执行：

```bash
export GM_BROWSER_EVIDENCE_DIR="$(mktemp -d /tmp/globemind-browser-evidence.XXXXXX)"
PYTHONDONTWRITEBYTECODE=1 \
  /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B \
  deploy/browser_smoke.py \
  --base-url http://127.0.0.1:18091 \
  --output-dir "$GM_BROWSER_EVIDENCE_DIR" \
  --chromium-executable /absolute/path/to/chromium
export GM_BROWSER_REPORT="$GM_BROWSER_EVIDENCE_DIR/browser-smoke.json"
export GM_BROWSER_REPORT_SHA="$(sha256sum "$GM_BROWSER_REPORT" | cut -d' ' -f1)"
PYTHONDONTWRITEBYTECODE=1 \
  /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from deploy.browser_smoke_evidence import verify_browser_smoke_evidence

receipt = verify_browser_smoke_evidence(
    Path(os.environ["GM_BROWSER_REPORT"]),
    expected_report_sha256=os.environ["GM_BROWSER_REPORT_SHA"],
    evaluated_at=datetime.now(timezone.utc),
)
print(json.dumps(receipt, indent=2, sort_keys=True))
PY
```

通过只表示 browser evidence 的范围、哈希和已声明语义重新核验通过；由于使用内存 stub，`candidate_acceptance=not_established_in_memory_stubs_only`。真实 API 一致性、物理设备、键盘、触控、读屏、WCAG 和发布仍需人工/外部验收。
