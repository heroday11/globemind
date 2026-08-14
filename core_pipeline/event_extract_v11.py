"""
Political news event extraction via vLLM.

The current stream pipeline uses the v2 schema:
  domain, event_family, event_action, initiator, target, location, tone.

Legacy fields event_type and trigger_verb are kept optional for old checkpoints and
older L1 code paths, but are no longer requested from the LLM in the fast extractor.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("event_extract_v11")

VLLM_BASE = os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8004")
VLLM_URL = f"{VLLM_BASE.rstrip('/')}/v1/chat/completions"
# The served model is deployment-specific; keep a portable identifier and let
# the runtime override it explicitly.
MODEL_ID = os.environ.get("VLLM_MODEL_NAME", "Qwen2.5-7B-Instruct-AWQ")

MAX_INPUT_CHARS = 400    # 标题 + 正文前导语，新闻倒金字塔结构核心信息在前 1-2 段
MAX_OUTPUT_TOKENS = 120   # compact JSON; trigger_verb is no longer generated
SEMAPHORE_LIMIT = 80
CHECKPOINT_EVERY = 500

# ── Legacy event_type values kept for old checkpoints ───────
GEOPOLITICAL_EVENT_TYPES = frozenset({
    "trade_conflict",
    "diplomacy",
    "military",
    "policy_legal",
    "aid_disaster",
    "protest_repression",
    "appointment_leadership",
    "human_rights_migration",
    "terrorism_espionage",
})

GENERAL_NEWS_EVENT_TYPES = frozenset({
    "sports",
    "entertainment",
    "business",
    "science_tech",
    "health",
    "crime",
    "education",
    "environment",
    "other",
})

VALID_EVENT_TYPES = GEOPOLITICAL_EVENT_TYPES | GENERAL_NEWS_EVENT_TYPES

# 聚类时跳过这些类型
NON_GEOPOLITICAL = GENERAL_NEWS_EVENT_TYPES

DOMAIN_MAP: dict[str, str] = {}
for t in GEOPOLITICAL_EVENT_TYPES:
    DOMAIN_MAP[t] = "political"
for t in GENERAL_NEWS_EVENT_TYPES:
    DOMAIN_MAP[t] = "general_news"

# ── v2 taxonomy ─────────────────────────────────────────────
POLITICAL_DOMAINS = frozenset({"political", "geopolitical"})
VALID_DOMAINS = frozenset({"political", "general_news"})

EVENT_FAMILIES = frozenset({
    "diplomacy",
    "military_security",
    "economic_trade",
    "technology_industry",
    "domestic_politics",
    "law_policy",
    "civil_unrest",
    "human_rights_migration",
    "public_development",
    "security_crime",
    "disaster_environment",
    "general_non_political",
})

EVENT_ACTIONS = frozenset({
    "meeting_visit",
    "statement_condemnation",
    "agreement_signed",
    "negotiation_talks",
    "sanction_export_control",
    "tariff_trade_dispute",
    "industrial_policy",
    "technology_policy",
    "infrastructure_development",
    "public_welfare_policy",
    "military_attack",
    "military_deployment",
    "ceasefire_peace_talks",
    "election_vote",
    "leadership_change",
    "law_policy_change",
    "court_ruling",
    "protest",
    "crackdown_arrest",
    "aid_delivery",
    "migration_refugee",
    "terror_attack",
    "cyber_espionage",
    "environment_policy",
    "disaster_response",
    "other",
})

ACTION_FAMILY: dict[str, str] = {
    "meeting_visit": "diplomacy",
    "statement_condemnation": "diplomacy",
    "agreement_signed": "diplomacy",
    "negotiation_talks": "diplomacy",
    "sanction_export_control": "economic_trade",
    "tariff_trade_dispute": "economic_trade",
    "industrial_policy": "economic_trade",
    "technology_policy": "technology_industry",
    "infrastructure_development": "public_development",
    "public_welfare_policy": "public_development",
    "military_attack": "military_security",
    "military_deployment": "military_security",
    "ceasefire_peace_talks": "military_security",
    "election_vote": "domestic_politics",
    "leadership_change": "domestic_politics",
    "law_policy_change": "law_policy",
    "court_ruling": "law_policy",
    "protest": "civil_unrest",
    "crackdown_arrest": "civil_unrest",
    "aid_delivery": "human_rights_migration",
    "migration_refugee": "human_rights_migration",
    "terror_attack": "security_crime",
    "cyber_espionage": "security_crime",
    "environment_policy": "disaster_environment",
    "disaster_response": "disaster_environment",
    "other": "general_non_political",
}

ACTION_ALLOWED_FAMILIES: dict[str, frozenset[str]] = {
    action: frozenset({family}) for action, family in ACTION_FAMILY.items()
}
ACTION_ALLOWED_FAMILIES.update({
    "meeting_visit": frozenset({"diplomacy", "domestic_politics", "economic_trade", "military_security"}),
    "statement_condemnation": frozenset({"diplomacy", "domestic_politics", "military_security"}),
    "agreement_signed": frozenset({"diplomacy", "economic_trade", "military_security", "public_development"}),
    "negotiation_talks": frozenset({"diplomacy", "economic_trade", "military_security", "law_policy"}),
    "sanction_export_control": frozenset({"economic_trade", "technology_industry", "military_security", "security_crime"}),
    "industrial_policy": frozenset({"economic_trade", "technology_industry", "public_development"}),
    "technology_policy": frozenset({"technology_industry", "law_policy"}),
    "infrastructure_development": frozenset({"public_development", "economic_trade"}),
    "public_welfare_policy": frozenset({"public_development", "law_policy", "domestic_politics"}),
    "law_policy_change": frozenset({
        "law_policy", "domestic_politics", "economic_trade", "technology_industry", "public_development",
    }),
    "court_ruling": frozenset({"law_policy", "domestic_politics"}),
    "protest": frozenset({"civil_unrest", "domestic_politics"}),
    "crackdown_arrest": frozenset({"civil_unrest", "security_crime", "law_policy"}),
    "aid_delivery": frozenset({"human_rights_migration", "disaster_environment", "diplomacy", "public_development"}),
    "migration_refugee": frozenset({"human_rights_migration", "law_policy"}),
    "environment_policy": frozenset({"disaster_environment", "public_development", "law_policy"}),
    "disaster_response": frozenset({"disaster_environment", "public_development"}),
})

ACTION_ALIASES: dict[str, str] = {
    "legislation": "law_policy_change",
    "bill": "law_policy_change",
    "bill_passed": "law_policy_change",
    "regulation": "law_policy_change",
    "regulatory_policy": "law_policy_change",
    "export_control": "sanction_export_control",
    "export_controls": "sanction_export_control",
    "sanction": "sanction_export_control",
    "sanctions": "sanction_export_control",
    "tariff": "tariff_trade_dispute",
    "tariffs": "tariff_trade_dispute",
    "trade_talks": "negotiation_talks",
    "talks": "negotiation_talks",
    "meeting": "meeting_visit",
    "visit": "meeting_visit",
    "election": "election_vote",
    "appointment": "leadership_change",
    "military_strike": "military_attack",
    "attack": "military_attack",
    "arrest": "crackdown_arrest",
    "crackdown": "crackdown_arrest",
    "refugee_migration": "migration_refugee",
    "climate_policy": "environment_policy",
    "environmental_policy": "environment_policy",
    "forest_fund": "environment_policy",
    "climate_fund": "environment_policy",
    "fund_raising": "environment_policy",
    "fundraising": "environment_policy",
    "disaster_aid": "disaster_response",
}

LEGACY_TYPE_TO_V2: dict[str, tuple[str, str]] = {
    "trade_conflict": ("economic_trade", "tariff_trade_dispute"),
    "diplomacy": ("diplomacy", "meeting_visit"),
    "military": ("military_security", "military_attack"),
    "policy_legal": ("law_policy", "law_policy_change"),
    "aid_disaster": ("disaster_environment", "disaster_response"),
    "protest_repression": ("civil_unrest", "protest"),
    "appointment_leadership": ("domestic_politics", "leadership_change"),
    "human_rights_migration": ("human_rights_migration", "migration_refugee"),
    "terrorism_espionage": ("security_crime", "terror_attack"),
}

# Trigger 默认值（向后兼容，general_news 用占位符）
_DEFAULT_TRIGGERS: dict[str, str] = {
    # 地缘政治
    "trade_conflict": "imposes sanctions or cooperates on trade",
    "diplomacy": "engages in diplomatic activities",
    "military": "engages in military activities",
    "policy_legal": "enacts policy or legal action",
    "aid_disaster": "provides aid or responds to disaster",
    "protest_repression": "protests or represses",
    "appointment_leadership": "changes leadership",
    "human_rights_migration": "addresses human rights or migration",
    "terrorism_espionage": "conducts terrorism or espionage",
    # 通用新闻（不参与聚类）
    "sports": "reports sports event",
    "entertainment": "reports entertainment event",
    "business": "reports business event",
    "science_tech": "reports science or technology event",
    "health": "reports health event",
    "crime": "reports crime event",
    "education": "reports education event",
    "environment": "reports environment event",
    "other": "reports general event",
}


# ── Prompt ─────────────────────────────────────────────────
SYSTEM_PROMPT = "你是新闻政治事件结构化抽取器。只输出一行紧凑 JSON，不解释。"

USER_TEMPLATE = """JSON only.

domain:
- political: government/state/party/leader/military/police/court/regulator/central bank/IO, public policy/budget/fund, election/protest/rights/migration, sanction/tariff/export control/industrial policy, national security/international relations, or policy/security-driven economy/trade/energy/tech/development/welfare/climate/environment.
- general_news: sports/entertainment/lifestyle, company earnings/product/market, health/education/local crime/weather/accident/disaster, with no policy/security/governance/international relation.

event_family = diplomacy, military_security, economic_trade, technology_industry, domestic_politics, law_policy, civil_unrest, human_rights_migration, public_development, security_crime, disaster_environment, general_non_political
event_action = meeting_visit, statement_condemnation, agreement_signed, negotiation_talks, sanction_export_control, tariff_trade_dispute, industrial_policy, technology_policy, infrastructure_development, public_welfare_policy, military_attack, military_deployment, ceasefire_peace_talks, election_vote, leadership_change, law_policy_change, court_ruling, protest, crackdown_arrest, aid_delivery, migration_refugee, terror_attack, cyber_espionage, environment_policy, disaster_response, other

Rules: action from list only. bill/regulation/new rule -> law_policy_change; AI/chip/data policy -> technology_policy; climate/forest/environment fund -> environment_policy. general_news => general_non_political/other and initiator=target=null.
Keys: domain,event_family,event_action,initiator,target,location,tone. Core names only. location country/region/city/null. tone positive|negative|neutral.

Examples:
US-China trade talks → {{"domain":"political","event_family":"economic_trade","event_action":"negotiation_talks","initiator":"US","target":"China","location":null,"tone":"neutral"}}
Football final → {{"domain":"general_news","event_family":"general_non_political","event_action":"other","initiator":null,"target":null,"location":null,"tone":"positive"}}

News:
{text}"""

ASSISTANT_PREFILL = '{"domain":"'


# ── Data classes ────────────────────────────────────────────
@dataclass
class Event:
    domain: str
    initiator: Optional[str]
    target: Optional[str]
    event_type: Optional[str] = None          # legacy
    event_family: Optional[str] = None
    event_action: Optional[str] = None
    trigger_verb: Optional[str] = None
    location: Optional[str] = None
    tone: str = "neutral"

    @property
    def trigger(self) -> str:
        """向后兼容 — 优先返回 trigger_verb，回退到模板。"""
        if self.trigger_verb:
            return self.trigger_verb
        if self.event_action:
            return self.event_action.replace("_", " ")
        return _DEFAULT_TRIGGERS.get(self.event_type or "", "takes action")


@dataclass
class ExtractionResult:
    article_id: int
    published_at: Optional[str]
    event: Optional[Event]
    raw_response: str
    parse_success: bool
    error: Optional[str] = None


# ── Payload ─────────────────────────────────────────────────
def _build_payload(text: str) -> Dict[str, Any]:
    return {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(text=text[:MAX_INPUT_CHARS])},
            {"role": "assistant", "content": ASSISTANT_PREFILL},
        ],
        "temperature": 0.0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stop": ["\n\n", "<|endoftext|>"],
    }


# ── Parse ───────────────────────────────────────────────────
def _coerce_event(raw: str) -> Tuple[Optional[Event], str]:
    """解析 LLM 输出为 Event。返回 (event, cleaned_raw)。"""
    cleaned = raw.strip()
    # 去掉 markdown fence
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)

    obj = None
    for attempt in range(3):
        try:
            obj = json.loads(cleaned)
            break
        except json.JSONDecodeError:
            if attempt == 0:
                # 找第一个 { 和最后一个 }
                start = cleaned.find('{')
                end = cleaned.rfind('}')
                if start >= 0 and end > start:
                    cleaned = cleaned[start:end + 1]
                else:
                    break
            elif attempt == 1:
                # 修复常见问题
                cleaned = re.sub(r',\s*}', '}', cleaned)
                cleaned = re.sub(r',\s*]', ']', cleaned)
            else:
                break

    if not isinstance(obj, dict):
        # Regex fallback
        obj = {}
        for field in (
            "domain", "event_family", "event_action", "initiator", "target",
            "location", "tone", "event_type", "trigger_verb",
        ):
            m = re.search(r'"' + field + r'"\s*:\s*"([^"]*)"', cleaned)
            if m:
                val = m.group(1).strip()
                if val and val.lower() not in ("null", "none"):
                    obj[field] = val
        # null-valued fields
        for field in ("initiator", "target", "trigger_verb", "location"):
            if field not in obj and re.search(r'"' + field + r'"\s*:\s*null', cleaned):
                obj[field] = None
        if not obj.get("domain") and obj.get("event_family"):
            obj["domain"] = "general_news" if obj["event_family"] == "general_non_political" else "political"
        if not obj.get("event_family") and obj.get("event_type"):
            obj["event_family"], obj["event_action"] = LEGACY_TYPE_TO_V2.get(
                str(obj["event_type"]).strip().lower(), ("general_non_political", "other")
            )
        if not obj.get("domain") or not obj.get("event_family"):
            return None, cleaned

    domain = str(obj.get("domain") or "").strip().lower()
    event_type = str(obj.get("event_type") or "").strip().lower() or None
    event_family = str(obj.get("event_family") or "").strip().lower()
    event_action = str(obj.get("event_action") or "").strip().lower()

    # Legacy output fallback.
    if (not event_family or not event_action) and event_type:
        event_family, event_action = LEGACY_TYPE_TO_V2.get(
            event_type, ("general_non_political", "other")
        )

    if event_action not in EVENT_ACTIONS:
        event_action = ACTION_ALIASES.get(event_action, "other")
    if event_action == "law_policy_change" and event_family == "technology_industry":
        event_action = "technology_policy"
    if event_family not in EVENT_FAMILIES:
        event_family = ACTION_FAMILY.get(event_action, "general_non_political")

    default_family = ACTION_FAMILY.get(event_action)
    allowed_families = ACTION_ALLOWED_FAMILIES.get(event_action, frozenset({default_family} if default_family else ()))
    if event_action != "other" and allowed_families and event_family not in allowed_families:
        event_family = default_family or "general_non_political"

    # Normalize legacy geopolitical into the broader political domain.
    if domain in POLITICAL_DOMAINS:
        domain = "political"
    elif domain != "general_news":
        domain = "general_news" if event_family == "general_non_political" else "political"

    if event_family == "general_non_political":
        domain = "general_news"
        event_action = "other"
    elif domain == "general_news":
        event_family = "general_non_political"
        event_action = "other"

    def _nullify(v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip().rstrip('.')
        if s.lower() in ("null", "none", "", "unknown"):
            return None
        return s

    # general_news 时 initiator/target 强制 null
    initiator = _nullify(obj.get("initiator"))
    target = _nullify(obj.get("target"))
    if domain == "general_news":
        initiator = None
        target = None

    # Optional legacy/evidence fields.
    trigger_verb = _nullify(obj.get("trigger_verb"))
    location = _nullify(obj.get("location"))
    tone = str(obj.get("tone") or "neutral").strip().lower()
    if tone not in ("positive", "negative", "neutral"):
        tone = "neutral"

    return Event(
        domain=domain,
        initiator=initiator,
        target=target,
        event_type=event_type,
        event_family=event_family,
        event_action=event_action,
        trigger_verb=trigger_verb,
        location=location,
        tone=tone,
    ), cleaned


# ── Extraction ──────────────────────────────────────────────
async def extract_one(
    session,  # aiohttp.ClientSession
    sem: asyncio.Semaphore,
    article_id: int,
    text: str,
    published_at: Optional[str] = None,
    max_retries: int = 3,
) -> ExtractionResult:
    """从单篇文章提取事件。"""
    import aiohttp
    payload = _build_payload(text)

    for attempt in range(max_retries):
        try:
            async with sem:
                async with session.post(VLLM_URL, json=payload) as resp:
                    raw_text = await resp.text()

            if resp.status >= 400:
                if resp.status == 400 and attempt < max_retries - 1:
                    wait = 1.5 ** attempt
                    logger.warning("vLLM 400, retrying in %.1fs (article %s)", wait, article_id)
                    await asyncio.sleep(wait)
                    payload["messages"][1]["content"] = text[:max(300, MAX_INPUT_CHARS // (2 ** (attempt + 1)))]
                    continue
                raise RuntimeError(f"vLLM HTTP {resp.status}: {raw_text[:300]}")

            data = json.loads(raw_text)
            content_raw = str((data.get("choices") or [{}])[0].get("message", {}).get("content") or "")

            # 接回 prefill（vLLM 输出不包含 prefill 部分）
            if not content_raw.startswith("{"):
                content_raw = re.sub(r'^"', '', content_raw)
                # 模型有时输出 "general_news":"other"（带多余 key），修复为纯值
                content_raw = re.sub(r'^(general_news|political|geopolitical)":"\w+', r'\1', content_raw)
                # 模型跳过 domain 直接输出后续字段
                if content_raw.startswith(("event_family", "event_action")):
                    content_raw = '{"domain":"political","' + content_raw
                elif content_raw.startswith("event_type"):
                    content_raw = '{"domain":"political","' + content_raw
                elif content_raw.startswith(("political", "geopolitical", "general_news")):
                    content_raw = ASSISTANT_PREFILL + content_raw
                else:
                    content_raw = ASSISTANT_PREFILL + content_raw

            # 修复双引号问题：{"domain":""geopolitical"...
            content_raw = re.sub(r'\{"domain":\s*""', '{"domain":"', content_raw)

            event, cleaned = _coerce_event(content_raw)
            return ExtractionResult(
                article_id=article_id,
                published_at=published_at,
                event=event,
                raw_response=content_raw[:300],
                parse_success=event is not None,
            )

        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError, RuntimeError) as e:
            if attempt < max_retries - 1:
                wait = 1.5 ** attempt
                logger.debug("vLLM error (article %s, attempt %s): %s; retry in %.1fs", article_id, attempt, e, wait)
                await asyncio.sleep(wait)
            else:
                logger.warning("vLLM failed (article %s): %s", article_id, e)
                return ExtractionResult(
                    article_id=article_id,
                    published_at=published_at,
                    event=None,
                    raw_response=str(e)[:300],
                    parse_success=False,
                    error=str(e)[:200],
                )

    return ExtractionResult(
        article_id=article_id, published_at=published_at,
        event=None, raw_response="", parse_success=False,
    )


# ── Checkpoint ──────────────────────────────────────────────
def load_checkpoint(path: str) -> Dict[int, ExtractionResult]:
    results: Dict[int, ExtractionResult] = {}
    if not os.path.isfile(path):
        return results
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                ev = None
                if d.get("event"):
                    ev = Event(**d["event"])
                aid = int(d["article_id"])
                results[aid] = ExtractionResult(
                    article_id=aid,
                    published_at=d.get("published_at"),
                    event=ev,
                    raw_response=d.get("raw_response", ""),
                    parse_success=d.get("parse_success", False),
                    error=d.get("error"),
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return results


def save_checkpoint(path: str, result: ExtractionResult):
    d = asdict(result)
    d["event"] = asdict(result.event) if result.event else None
    with open(path, "a") as f:
        f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")


async def extract_batch(
    articles: List[Dict[str, Any]],
    checkpoint_path: str,
    text_field: str = "text",
    max_concurrent: int = 80,
) -> List[ExtractionResult]:
    """批量提取，含 checkpoint/resume + tqdm 进度条 + ETA。

    max_concurrent: 默认 80（A30 24GB, max-model-len=2048 时安全值）。
    """
    import aiohttp
    from tqdm import tqdm

    done = load_checkpoint(checkpoint_path)
    n_resumed = len(done)
    if n_resumed:
        logger.info("Checkpoint loaded: %d done articles (resume mode)", n_resumed)

    sem = asyncio.Semaphore(max_concurrent)
    connector = aiohttp.TCPConnector(limit=max_concurrent + 8, ttl_dns_cache=300)
    write_lock = asyncio.Lock()
    results: List[ExtractionResult] = []

    async def extract_and_save(session, aid, text, published_at):
        result = await extract_one(session, sem, aid, text, published_at)
        async with write_lock:
            save_checkpoint(checkpoint_path, result)
        return result

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for article in articles:
            aid = int(article["id"])
            if aid in done:
                results.append(done[aid])
                continue

            text = (article.get(text_field) or "").strip()
            if not text:
                title = article.get("title") or ""
                body = article.get("body") or article.get("english_text") or ""
                text = f"{title}\n{body}".strip()
            if not text:
                results.append(ExtractionResult(
                    article_id=aid, published_at=article.get("published_at"),
                    event=None, raw_response="", parse_success=False, error="empty text",
                ))
                continue

            published_at = str(article.get("published_at") or "") if article.get("published_at") else None
            tasks.append(asyncio.create_task(
                extract_and_save(session, aid, text, published_at)
            ))

        n_skip = len(done)
        n_new = len(tasks)
        n_total = n_skip + n_new
        logger.info("Launching %d concurrent extraction tasks (resumed=%d, semaphore=%d)", n_new, n_skip, max_concurrent)

        pbar = tqdm(
            asyncio.as_completed(tasks),
            total=n_new,
            desc="Extracting",
            unit="art",
            position=0,
            leave=True,
            mininterval=30,
            miniters=100,
        )
        t0_extract = time.time()
        t_last_update = t0_extract
        ok = sum(1 for r in results if r.parse_success)
        fail = sum(1 for r in results if not r.parse_success)
        political = sum(1 for r in results if r.event and r.event.domain == "political")

        for completed in pbar:
            result = await completed
            results.append(result)

            if result.parse_success:
                ok += 1
            else:
                fail += 1
            if result.event and result.event.domain == "political":
                political += 1

            now = time.time()
            if ok > 0 and now - t_last_update >= 30.0:
                elapsed = now - t0_extract
                done_total = ok + fail
                rate = done_total / max(elapsed, 1)
                remaining = (n_total - done_total) / max(rate, 0.01)
                pbar.set_postfix(
                    ok=ok,
                    fail=fail,
                    political=political,
                    rate=f"{rate:.1f}/s",
                    eta=f"{remaining/3600:.1f}h",
                )
                t_last_update = now

    return results


def print_report(results: List[ExtractionResult]):
    """打印提取质量报告。"""
    total = len(results)
    ok = sum(1 for r in results if r.parse_success)
    fail = total - ok
    political = sum(1 for r in results if r.event and r.event.domain == "political")
    gen = sum(1 for r in results if r.event and r.event.domain == "general_news")
    print(f"\n{'='*60}")
    print(f"EVENT EXTRACTION v11 REPORT")
    print(f"{'='*60}")
    print(f"Total:       {total}")
    print(f"OK:          {ok} ({100*ok//max(total,1)}%)")
    print(f"Failed:      {fail} ({100*fail//max(total,1)}%)")
    print(f"Political:   {political} ({100*political//max(ok,1)}%)")
    print(f"General news:{gen} ({100*gen//max(ok,1)}%)")

    if ok == 0:
        return

    from collections import Counter
    families = Counter()
    actions = Counter()
    domains = Counter()
    null_init = null_tgt = 0
    for r in results:
        if not r.event:
            continue
        families[r.event.event_family or "unknown"] += 1
        actions[r.event.event_action or "unknown"] += 1
        domains[r.event.domain] += 1
        if not r.event.initiator:
            null_init += 1
        if not r.event.target:
            null_tgt += 1

    print(f"\nDomains ({len(domains)}):")
    for d, cnt in domains.most_common():
        print(f"  {d:20s}: {cnt:>4} ({100*cnt//ok}%)")

    print(f"\nEvent families ({len(families)} unique):")
    for family, cnt in families.most_common(15):
        print(f"  {family:30s}: {cnt:>4} ({100*cnt//ok}%)")

    print(f"\nEvent actions ({len(actions)} unique):")
    for action, cnt in actions.most_common(15):
        print(f"  {action:30s}: {cnt:>4} ({100*cnt//ok}%)")

    print(f"\nField coverage:")
    print(f"  event_family:     {sum(1 for r in results if r.event and r.event.event_family)}/{ok}")
    print(f"  event_action:     {sum(1 for r in results if r.event and r.event.event_action)}/{ok}")
    print(f"  initiator (non-null): {ok - null_init}/{ok} ({100*(ok-null_init)//max(ok,1)}%)")
    print(f"  target (non-null):    {ok - null_tgt}/{ok} ({100*(ok-null_tgt)//max(ok,1)}%)")
    print()
