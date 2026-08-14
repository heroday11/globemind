import json
import os
import re
import shutil
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

from config.settings import obsidian_vault_path
from agentic_rag.db_runtime_config import require_database_password

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# 默认写入 config.yaml paths.obsidian_vault（仓库根 obsidian_vault/）
DEFAULT_VAULT_DIR = obsidian_vault_path()
# 生成 Micro 笔记所需最少文章数（>1 篇即 >=2；可用环境变量 OBSIDIAN_MICRO_MIN_ARTICLES 覆盖）
_DEFAULT_MICRO_MIN = 2


def _obsidian_micro_min_articles() -> int:
    try:
        n = int(os.getenv("OBSIDIAN_MICRO_MIN_ARTICLES", str(_DEFAULT_MICRO_MIN)))
    except ValueError:
        n = _DEFAULT_MICRO_MIN
    return max(2, n)


def _obsidian_macro_min_linked_micros() -> int:
    """Macro 导出所需最少「可链接」Micro 数（Stage4 展示层过滤）。"""
    try:
        n = int(os.getenv("OBSIDIAN_MACRO_MIN_LINKED_MICROS", "1"))
    except ValueError:
        n = 1
    return max(0, n)


def _obsidian_sync_joint_dist_max() -> float:
    try:
        return float(os.getenv("OBSIDIAN_SYNC_JOINT_DIST_MAX", "1.0"))
    except ValueError:
        return 1.0


def _obsidian_entities_limits() -> tuple[int, int]:
    try:
        max_items = int(os.getenv("OBSIDIAN_ENTITIES_MAX_ITEMS", "30"))
    except ValueError:
        max_items = 30
    try:
        max_len = int(os.getenv("OBSIDIAN_ENTITIES_MAX_STR_LEN", "48"))
    except ValueError:
        max_len = 48
    return max(1, max_items), max(8, max_len)


def _obsidian_micro_stub_links() -> bool:
    """为未达 Micro 篇数阈值、但已归属某条宏观故事线的微事件生成占位笔记，便于 Macro↔Micro 双向 Wikilink。"""
    return os.getenv("OBSIDIAN_MICRO_STUB_LINKS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _micro_dir_and_prefix(vault: Path) -> tuple[Path, str]:
    """微事件笔记目录：固定为库根下 MicroEvents（不再生成顶层 Events/）。"""
    raw = (os.getenv("OBSIDIAN_MICRO_REL_PATH", "MicroEvents") or "MicroEvents").strip()

    norm = raw.replace("\\", "/").strip("/")
    if "@" in norm:
        return vault / "MicroEvents", "MicroEvents"

    if not norm or norm.lower() == "microevents":
        return vault / "MicroEvents", "MicroEvents"

    if norm == "Events/Micro":
        # 旧布局已弃用：避免创建 obsidian_vault/Events，与 MacroEvents / MicroEvents / Articles 一致
        return vault / "MicroEvents", "MicroEvents"

    return vault / "MicroEvents", "MicroEvents"

def format_pub_date_for_yaml(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.date().isoformat()
    if isinstance(dt, date):
        return dt.isoformat()
    s = str(dt).strip()
    if "T" in s and len(s) > 10:
        return s.split("T", 1)[0]
    if " " in s:
        return s.split()[0]
    return s


def _normalize_text_key(val: str) -> str:
    return re.sub(r"\s+", "", str(val or "").strip())


def _attach_news_by_title_date_fallback(micros: list, news_rows: list, micro_news: dict) -> int:
    """Fallback attach for broken event_id linkage using title/date heuristics."""
    if not micros or not news_rows:
        return 0
    skip = os.getenv("OBSIDIAN_SKIP_ORPHAN_FALLBACK", "").strip().lower()
    if skip in ("1", "true", "yes", "on"):
        return 0

    def _to_day(v):
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        s = format_pub_date_for_yaml(v)
        try:
            return datetime.fromisoformat(s).date()
        except Exception:
            return None

    def _ascii_tokens(v: str) -> set[str]:
        return {t.upper() for t in re.findall(r"[A-Za-z0-9]{3,}", str(v or ""))}

    attached = 0
    seen_news_ids = {
        int(x.get("news_id"))
        for items in micro_news.values()
        for x in items
        if x.get("news_id") is not None
    }

    pre = []
    by_token = defaultdict(list)
    for n in news_rows:
        nid = n.get("news_id")
        if nid is None:
            continue
        if n.get("event_id") is not None:
            continue
        try:
            nid_int = int(nid)
        except (TypeError, ValueError):
            continue
        if nid_int in seen_news_ids:
            continue
        n_title = str(n.get("title") or "")
        n_key = _normalize_text_key(n_title)
        n_tokens = _ascii_tokens(n_title)
        n_day = _to_day(n.get("pub_time"))
        idx = len(pre)
        pre.append(
            {
                "n": n,
                "nid": nid_int,
                "n_key": n_key,
                "n_tokens": n_tokens,
                "n_day": n_day,
            }
        )
        for t in n_tokens:
            by_token[t].append(idx)

    for m in micros:
        eid = m["event_id"]
        target = int(m.get("article_count") or 0)
        miss = target - len(micro_news.get(eid, []))
        if miss <= 0:
            continue

        m_title = str(m.get("title") or "")
        m_key = _normalize_text_key(m_title)
        m_tokens = _ascii_tokens(m_title)
        m_day = _to_day(m.get("start_date"))

        cand_idx = set()
        if m_tokens:
            for t in m_tokens:
                cand_idx.update(by_token.get(t, ()))
        if not cand_idx and m_key:
            for i, p in enumerate(pre):
                nk = p["n_key"]
                if nk and m_key and (m_key in nk or nk in m_key):
                    cand_idx.add(i)

        candidates = []
        iter_idx = cand_idx if cand_idx else range(len(pre))
        for i in iter_idx:
            p = pre[i]
            n = p["n"]
            nid = p["nid"]
            if nid in seen_news_ids:
                continue

            n_key = p["n_key"]
            n_tokens = p["n_tokens"]
            n_day = p["n_day"]

            title_hit = False
            if m_key and n_key and (m_key in n_key or n_key in m_key):
                title_hit = True
            elif m_tokens and n_tokens and (m_tokens & n_tokens):
                title_hit = True
            if not title_hit:
                continue

            day_gap = 999
            if m_day is not None and n_day is not None:
                day_gap = abs((n_day - m_day).days)
                if day_gap > 7:
                    continue

            candidates.append((day_gap, nid, n))

        candidates.sort(key=lambda x: (x[0], x[1]))
        for _, nid, n in candidates:
            if nid in seen_news_ids:
                continue
            micro_news[eid].append(n)
            seen_news_ids.add(nid)
            attached += 1
            if len(micro_news[eid]) >= target:
                break

    return attached

def _fmt_china_index_display(val) -> str:
    if val is None:
        return "—"
    try:
        x = round(float(val), 4)
        t = f"{x:.4f}".rstrip("0").rstrip(".")
        return t if t else "0"
    except (TypeError, ValueError):
        return str(val)


_INTEL_DIST_FALLBACK = "（不计算）"


def _truncate_entities_pool_for_yaml(ep) -> tuple[list, bool]:
    """返回 (列表项, 是否截断)；剔除换行与过长噪声。"""
    max_items, max_len = _obsidian_entities_limits()
    truncated = False

    def _normalize_items(obj):
        if obj is None:
            return []
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except Exception:
                return [obj]
        if isinstance(obj, dict):
            return [f"{k}:{v}" for k, v in obj.items()]
        if isinstance(obj, (list, tuple)):
            return list(obj)
        return [str(obj)]

    raw_list = _normalize_items(ep)
    out: list = []
    for x in raw_list[:max_items]:
        s = str(x).strip().replace("\n", " ").replace("\r", "")
        if len(s) > max_len:
            s = s[:max_len]
            truncated = True
        if s:
            out.append(s)
    if len(raw_list) > max_items:
        truncated = True
    return out, truncated


def _fmt_macro_sub_line(
    se: dict,
    micro_filenames: dict,
    rel_micro_prefix: str,
) -> str:
    """有独立微笔记则用 Wikilink，否则纯文本（避免空链接）。"""
    eid = se["event_id"]
    ac = int(se.get("article_count") or 0)
    tit = (se.get("title") or f"微簇{eid}").strip()
    d0 = se.get("start_date") or ""
    extra = f" · {ac}篇"
    if eid in micro_filenames:
        ef = micro_filenames[eid]
        return (
            f"- [[{rel_micro_prefix}/{ef[:-3]}|{tit}]] "
            f"({d0}){extra}"
        )
    return f"- {tit} （{d0}）{extra}（无独立微笔记）"


def _safe_filename(text):
    return re.sub(r'[\\/:*?"<>|]', '', str(text or '')).strip()[:50]


def _yaml_title_line(text) -> str:
    """frontmatter 用双引号包裹 title，内部双引号改为单引号，避免 f-string 嵌套引号语法错误。"""
    inner = str(text or "").replace('"', "'")
    return f'title: "{inner}"'


def _yaml_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _clean_intel_label(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    su = s.upper()
    if (
        not s
        or su == "PARSE_FAILED"
        or "解析失败" in s
        or "未标注" in s
        or "未计算" in s
    ):
        return None
    return s


def _append_parent_storylines_frontmatter(lines: list, parent_sids: list[int]) -> None:
    """多重宏观归属：YAML 列表（与 DB storyline_micro_map 一致）。"""
    if not parent_sids:
        return
    lines.append("parent_storyline_ids:")
    for sid in parent_sids:
        lines.append(f"  - {int(sid)}")


def _macro_parent_body_block(parent_sids: list[int], macro_titles: dict) -> str:
    """Micro 笔记正文：链向全部父宏观（Obsidian 双向链接）。"""
    links: list[str] = []
    for sid in parent_sids:
        if sid not in macro_titles:
            continue
        macro_safe = _safe_filename(macro_titles[sid])
        t = macro_titles[sid]
        links.append(f"[[MacroEvents/{macro_safe}|{t}]]")
    if not links:
        return ""
    if len(links) == 1:
        return f"\n**所属大事件**：{links[0]}"
    return "\n**所属大事件**（多重归属）\n" + "\n".join(f"- {x}" for x in links)


def _append_intel_frontmatter(lines: list, row: dict) -> None:
    ci = _yaml_float(row.get("china_index_avg"))
    if ci is not None:
        lines.append(f"china_index_avg: {round(ci, 4)}")
    else:
        lines.append("china_index_avg: null")
    sm = row.get("sentiment_main")
    if sm:
        inner = str(sm).replace('"', "'")
        lines.append(f'sentiment_main: "{inner}"')
    else:
        lines.append('sentiment_main: ""')
    tm = row.get("topic_main")
    if tm:
        inner = str(tm).replace('"', "'")
        lines.append(f'topic_main: "{inner}"')
    else:
        lines.append('topic_main: ""')
    ep = row.get("entities_pool")
    if ep is not None:
        pool, trunc = _truncate_entities_pool_for_yaml(ep)
        try:
            lines.append("entities_pool_json: " + json.dumps(pool, ensure_ascii=False))
            if trunc:
                lines.append('entities_pool_note: "truncated"')
        except Exception:
            lines.append("entities_pool_json: []")
    else:
        lines.append("entities_pool_json: []")


def _news_intel_counts(news_items: list):
    sc: Counter = Counter()
    tc: Counter = Counter()
    chinas: list[float] = []
    fb = _INTEL_DIST_FALLBACK
    for n in news_items:
        sv = _clean_intel_label(n.get("sentiment_analysis"))
        sc[sv if sv else fb] += 1
        tv = _clean_intel_label(n.get("topic_classification"))
        tc[tv if tv else fb] += 1
        ci = n.get("china_related_index")
        if ci is not None:
            try:
                chinas.append(float(ci))
            except (TypeError, ValueError):
                pass
    return sc, tc, chinas


def _dist_table_md(dist: dict) -> str:
    if not dist:
        return "_暂无有效统计（请确认已运行 Stage2 分析入库）_\n"
    lines = ["| 标签 | 篇数 |", "|---|---|"]
    for k, v in sorted(dist.items(), key=lambda x: (-x[1], str(x[0]))):
        lines.append(f"| {k} | {v} |")
    return "\n".join(lines) + "\n"


def _format_macro_sentiment_score_label(avg: float) -> str:
    """由 [-1,1] 均分生成可读标签（与 news_analysis.sentiment_score 一致）。"""
    if avg > 0.15:
        return f"偏正向 ({avg:+.2f})"
    if avg < -0.15:
        return f"偏负向 ({avg:+.2f})"
    return f"中性 ({avg:+.2f})"


def _sample_opinion_trend_points(items: list, max_points: int = 28) -> list:
    n = len(items)
    if n <= max_points:
        return list(items)
    step = (n - 1) / (max_points - 1)
    out = []
    for j in range(max_points):
        i = int(round(j * step))
        if i >= n:
            i = n - 1
        out.append(items[i])
    return out


def _trim_opinion_trend_items(items: list, *, eps: float = 1e-4, tail_keep: int = 7) -> list:
    """
    去掉舆情曲线尾部“长期近零”段，避免笔记中出现大段无信息衰减尾巴。
    保留最后一个显著点之后 tail_keep 天。
    """
    if not items:
        return []
    last_sig = -1
    for i, x in enumerate(items):
        try:
            v = float(x.get("impact") or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        if abs(v) >= eps:
            last_sig = i
    if last_sig < 0:
        # 全部近零时仅保留首点，避免空图
        return items[:1]
    end = min(len(items), last_sig + 1 + max(0, int(tail_keep)))
    return items[:end]


def _macro_opinion_trend_chart_block(raw) -> str:
    """Mermaid xychart-beta（抽样）+ 文本柱（全日程），供 Obsidian 渲染。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return ""
        try:
            raw = json.loads(s)
        except Exception:
            return ""
    if not isinstance(raw, list) or len(raw) == 0:
        return ""
    items = [x for x in raw if isinstance(x, dict)]
    if not items:
        return ""
    items = _trim_opinion_trend_items(items, eps=1e-4, tail_keep=7)
    if not items:
        return ""
    sample = _sample_opinion_trend_points(items, max_points=28)
    impacts = [float(x.get("impact") or 0) for x in sample]
    labels = []
    for x in sample:
        d = str(x.get("date") or "")
        labels.append(d[5:10] if len(d) >= 10 else d)
    y_min = min(0.0, min(impacts) * 1.05) if impacts else 0.0
    y_max = max(0.01, max(impacts) * 1.05) if impacts else 1.0
    if y_min >= y_max:
        y_max = y_min + 1e-6
    x_labels_json = json.dumps(labels, ensure_ascii=False)
    # Mermaid xychart 对科学计数法兼容差，强制固定小数格式。
    line_vals = "[" + ", ".join(f"{v:.6f}" for v in impacts) + "]"
    mermaid = (
        "```mermaid\n"
        "xychart-beta\n"
        '    title "舆情影响指数（日度，抽样）"\n'
        f"    x-axis {x_labels_json}\n"
        f'    y-axis "Impact" {y_min:.6f} --> {y_max:.6f}\n'
        f"    line {line_vals}\n"
        "```\n"
    )
    mx = max(abs(float(x.get("impact") or 0)) for x in items) or 1e-9
    w = 40
    ascii_lines = []
    for x in items:
        d = str(x.get("date") or "")
        v = float(x.get("impact") or 0)
        n_blk = int(round(w * min(abs(v) / mx, 1.0)))
        bar = "█" * n_blk
        ascii_lines.append(f"{d} │{bar} {v:+.6f}")
    ascii_block = "```\n" + "\n".join(ascii_lines) + "\n```\n"
    return (
        "### 舆情态势演化图\n\n"
        "_λ=0.1（约每 7 天衰减一半）；日度指数 = Σ(情感×信源可信度×涉华×e^{-0.1·Δt天})，Δt 为报道日之后经过的天数。_\n\n"
        + mermaid
        + "\n**全日程文本柱状图**\n\n"
        + ascii_block
        + "\n"
    )


def _depth_micro_section(m: dict, news_items: list) -> str:
    sc, tc, chinas = _news_intel_counts(news_items)
    cia_db = _yaml_float(m.get("china_index_avg"))
    cia_calc = float(sum(chinas) / len(chinas)) if chinas else None
    cia_show = cia_db if cia_db is not None else cia_calc
    dom_s = _clean_intel_label(m.get("sentiment_main")) or (sc.most_common(1)[0][0] if sc else "—")
    dom_t = _clean_intel_label(m.get("topic_main")) or (tc.most_common(1)[0][0] if tc else "—")
    if dom_s is None or dom_s == "":
        dom_s = "—"
    if dom_t is None or dom_t == "":
        dom_t = "—"
    no_intel = (not sc and not tc and cia_show is None)
    if no_intel:
        return "\n".join(
            [
                "## 深度研判",
                "",
                "> 该微事件当前缺少可用的涉华/情感/话题分析结果（常见于非涉华或未进入 Stage1b 分析范围）。",
                "> 这不代表数据错误；如需补齐，可降低涉华门槛或扩大 Stage1b 分析范围后重跑。",
                "",
            ]
        )
    parts = [
        "## 深度研判",
        "",
        "### 涉华权重与主导标签",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 平均涉华指数 | {_fmt_china_index_display(cia_show)} |",
        f"| 主导情感 | {dom_s} |",
        f"| 核心话题 | {dom_t} |",
        "",
        "### 情感分布（成员新闻）",
        _dist_table_md(dict(sc)),
        "### 话题分布（成员新闻）",
        _dist_table_md(dict(tc)),
        "### 说明",
        "字段优先来自数据库（Stage 5 聚合）；分布由当前成员新闻统计。更多正文在各关联文章。",
        "",
    ]
    return "\n".join(parts)


def _depth_macro_section(m: dict, subs: list, micro_news: dict) -> str:
    all_news: list = []
    for se in subs:
        all_news.extend(micro_news.get(se["event_id"], []))
    sc, tc, chinas = _news_intel_counts(all_news)
    cia_db = _yaml_float(m.get("china_index_avg"))
    cia_calc = float(sum(chinas) / len(chinas)) if chinas else None
    cia_show = cia_db if cia_db is not None else cia_calc
    score_vals: list[float] = []
    for n in all_news:
        v = n.get("sentiment_score")
        if v is None:
            continue
        try:
            score_vals.append(float(v))
        except (TypeError, ValueError):
            continue
    avg_sent = float(sum(score_vals) / len(score_vals)) if score_vals else None
    if avg_sent is not None:
        dom_s = _format_macro_sentiment_score_label(avg_sent)
    else:
        dom_s = _clean_intel_label(m.get("sentiment_main")) or (sc.most_common(1)[0][0] if sc else "—")
    dom_t = _clean_intel_label(m.get("topic_main")) or (tc.most_common(1)[0][0] if tc else "—")
    if not dom_s:
        dom_s = "—"
    if not dom_t:
        dom_t = "—"
    no_intel = (not sc and not tc and cia_show is None and avg_sent is None)
    opinion_block = _macro_opinion_trend_chart_block(m.get("opinion_trend_json"))
    if no_intel:
        return "\n".join(
            [
                "## 深度研判",
                "",
                "> 该宏观事件当前缺少可用的涉华/情感/话题分析结果（通常由非涉华子事件聚合而成）。",
                "> 建议按需放宽 Stage1b 覆盖范围后再生成分析标签。",
                "",
            ]
        )
    parts = [
        "## 深度研判",
        "",
        "### 涉华权重与主导标签",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 平均涉华指数 | {_fmt_china_index_display(cia_show)} |",
        f"| 主导情感（均分） | {dom_s} |",
        f"| 核心话题 | {dom_t} |",
        "",
    ]
    if opinion_block:
        parts.append(opinion_block)
    parts.extend(
        [
            "### 情感分布（全量子事件新闻）",
            _dist_table_md(dict(sc)),
            "### 话题分布（全量子事件新闻）",
            _dist_table_md(dict(tc)),
            "",
        ]
    )
    return "\n".join(parts)


def _extract_frontmatter_title_and_id(md_path: Path, id_key: str) -> tuple[Optional[int], Optional[str]]:
    try:
        txt = md_path.read_text(encoding="utf-8")
    except Exception:
        return None, None
    m = re.match(r"^---\n(.*?)\n---", txt, flags=re.DOTALL)
    if not m:
        return None, None
    fm = m.group(1)
    id_m = re.search(rf"^\s*{re.escape(id_key)}\s*:\s*(\d+)\s*$", fm, flags=re.MULTILINE)
    title_m = re.search(r'^\s*title\s*:\s*"(.*?)"\s*$', fm, flags=re.MULTILINE)
    sid = int(id_m.group(1)) if id_m else None
    title = title_m.group(1).strip() if title_m else None
    if title:
        title = title.replace("'", '"').strip()
    return sid, title


def sync_titles_from_obsidian_to_db(vault: Path) -> None:
    macro_dir = vault / "MacroEvents"
    micro_dir = vault / "MicroEvents"
    if not macro_dir.is_dir() and not micro_dir.is_dir():
        return

    macro_updates: list[tuple[str, int]] = []
    micro_updates: list[tuple[str, int]] = []

    if macro_dir.is_dir():
        for p in macro_dir.glob("*.md"):
            sid, title = _extract_frontmatter_title_and_id(p, "storyline_id")
            if sid is None or not title:
                continue
            macro_updates.append((title, sid))

    if micro_dir.is_dir():
        for p in micro_dir.glob("*.md"):
            eid, title = _extract_frontmatter_title_and_id(p, "event_id")
            if eid is None or not title:
                m = re.match(r"^E(\d+)_", p.stem)
                if m and title:
                    eid = int(m.group(1))
            if eid is None or not title:
                continue
            micro_updates.append((title, eid))

    if not macro_updates and not micro_updates:
        return

    conn = psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname="postgres",
        user=os.getenv("PG_WRITE_USER", os.getenv("PG_USER", "postgres")),
        password=os.getenv("PG_WRITE_PASSWORD", os.getenv("PG_PASSWORD", "")),
        connect_timeout=10,
    )
    try:
        with conn:
            with conn.cursor() as cur:
                if macro_updates:
                    cur.executemany(
                        """
                        UPDATE macro_storylines
                           SET title = %s
                         WHERE storyline_id = %s
                           AND COALESCE(title, '') <> %s
                        """,
                        [(title, sid, title) for (title, sid) in macro_updates],
                    )
                if micro_updates:
                    cur.executemany(
                        """
                        UPDATE micro_events
                           SET title = %s
                         WHERE event_id = %s
                           AND COALESCE(title, '') <> %s
                        """,
                        [(title, eid, title) for (title, eid) in micro_updates],
                    )
        print(
            f"[Obsidian] 本地标题反向同步至数据库：macro候选={len(macro_updates)} "
            f"micro候选={len(micro_updates)}"
        )
    finally:
        conn.close()


def _dedupe_micro_news(micro_news: dict) -> int:
    """按 event_id + news_id 去重，避免 UNION/兜底导致重复文章与统计偏差。"""
    removed = 0
    for eid, items in list(micro_news.items()):
        seen: set[int] = set()
        uniq: list = []
        for n in items:
            nid = n.get("news_id")
            if nid is None:
                uniq.append(n)
                continue
            try:
                nid_int = int(nid)
            except (TypeError, ValueError):
                uniq.append(n)
                continue
            if nid_int in seen:
                removed += 1
                continue
            seen.add(nid_int)
            uniq.append(n)
        micro_news[eid] = uniq
    return removed


def fetch_all_data():
    from agentic_rag.db.macro_schema import ensure_macro_storylines_optional_columns
    from agentic_rag.db.news_assignment_schema import ensure_news_assignment_table

    ensure_macro_storylines_optional_columns()
    ensure_news_assignment_table()

    joint_max = _obsidian_sync_joint_dist_max()
    print(
        f"[Obsidian] OBSIDIAN_SYNC_JOINT_DIST_MAX={joint_max}："
        "micro_event_members→news 含 joint_dist IS NULL 或 < 该阈值（路由器 max_joint_dist 可对照 incremental_router）"
    )

    conn = psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname="postgres",
        user=os.getenv("PG_USER", "news_reader"),
        password=require_database_password("PG_PASSWORD", "DB_PASSWORD"),
        connect_timeout=10,
    )
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'macro_storylines'
            """
        )
        macro_cols = {row["column_name"] for row in cur.fetchall()}
        has_macro_status = "status" in macro_cols
        has_description = "description" in macro_cols

        if has_macro_status:
            frag_filter = " AND COALESCE(status, 'active') != 'fragment' "
        elif has_description:
            frag_filter = (
                " AND (description IS NULL OR description NOT LIKE '【零碎线索】%') "
            )
        else:
            frag_filter = ""

        if not has_description:
            print(
                "[Obsidian] 提示: macro_storylines 尚无 description 列；"
                "宏观笔记「综述」将为空。已尝试用写库账号自动 ADD COLUMN（见 MACRO_AUTO_SCHEMA）。"
            )
        if not has_macro_status and not has_description:
            print(
                "[Obsidian] 提示: 无 status/description 时无法 SQL 过滤「零碎」宏观，"
                "将导出全部宏观行。"
            )

        macro_select_parts = [
            "storyline_id",
            "title",
            "start_date",
            "end_date",
            "micro_event_count",
            "article_count",
        ]
        if has_description:
            macro_select_parts.append("description")
        for col in (
            "china_index_avg",
            "sentiment_main",
            "topic_main",
            "entities_pool",
            "opinion_trend_json",
        ):
            if col in macro_cols and col not in macro_select_parts:
                macro_select_parts.append(col)
        select_cols = ", ".join(macro_select_parts)

        cur.execute(
            f"""
            SELECT {select_cols}
            FROM macro_storylines
            WHERE title IS NOT NULL
            {frag_filter}
            ORDER BY article_count DESC
            """
        )
        macros = cur.fetchall()

        if has_macro_status:
            _macro_export_note = (
                "已排除 macro_storylines.status=fragment"
            )
        elif has_description:
            _macro_export_note = (
                "已排除 description 以「【零碎线索】」开头的行"
            )
        else:
            _macro_export_note = "未按零碎规则过滤（无 status/description 列）"

        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'micro_events'
            """
        )
        micro_cols = {row["column_name"] for row in cur.fetchall()}
        micro_select_parts = ["event_id", "title", "start_date", "article_count"]
        for col in ("macro_storyline_id", "entities_pool", "china_index_avg", "sentiment_main", "topic_main"):
            if col in micro_cols and col not in micro_select_parts:
                micro_select_parts.append(col)
        micro_sql = ", ".join(micro_select_parts)
        cur.execute(
            f"""
            SELECT {micro_sql}
            FROM micro_events
            WHERE title IS NOT NULL
            ORDER BY start_date ASC
            """
        )
        micros = cur.fetchall()

        cur.execute("SELECT storyline_id, event_id FROM storyline_micro_map")
        mapping = cur.fetchall()

        cur.execute(
            """
            SELECT
                m.event_id,
                n.id as news_id,
                n.title,
                n.abstract,
                n.url,
                n.pub_time,
                na.sentiment_analysis,
                na.sentiment_score,
                na.is_china_related,
                na.china_related_index,
                na.topic_classification
            FROM micro_event_members m
            JOIN news n ON n.id = m.news_id
            LEFT JOIN news_analysis na ON na.news_id = n.id

            UNION ALL

            SELECT
                nas.micro_event_id AS event_id,
                n.id as news_id,
                n.title,
                n.abstract,
                n.url,
                n.pub_time,
                na.sentiment_analysis,
                na.sentiment_score,
                na.is_china_related,
                na.china_related_index,
                na.topic_classification
            FROM news n
            LEFT JOIN news_analysis na ON na.news_id = n.id
            LEFT JOIN news_assignment nas ON nas.news_id = n.id
            WHERE nas.micro_event_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM micro_event_members mm WHERE mm.news_id = n.id
              )

            UNION ALL

            SELECT
                me.event_id,
                n.id as news_id,
                n.title,
                n.abstract,
                n.url,
                n.pub_time,
                na.sentiment_analysis,
                na.sentiment_score,
                na.is_china_related,
                na.china_related_index,
                na.topic_classification
            FROM micro_events me
            JOIN news n
              ON n.title = me.title
             AND DATE(n.pub_time) = DATE(me.start_date)
            LEFT JOIN news_assignment nas ON nas.news_id = n.id
            LEFT JOIN news_analysis na ON na.news_id = n.id
            WHERE me.article_count >= 2
              AND nas.micro_event_id IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM micro_event_members mm
                  WHERE mm.event_id = me.event_id AND mm.news_id = n.id
              )
            """
        )
        news = cur.fetchall()

        cur.execute(
            """
            SELECT
                NULL::bigint AS event_id,
                n.id as news_id,
                n.title,
                n.abstract,
                n.url,
                n.pub_time,
                na.sentiment_analysis,
                na.sentiment_score,
                na.is_china_related,
                na.china_related_index,
                na.topic_classification
            FROM news n
            LEFT JOIN news_analysis na ON na.news_id = n.id
            LEFT JOIN news_assignment nas ON nas.news_id = n.id
            WHERE nas.micro_event_id IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM micro_event_members mm WHERE mm.news_id = n.id
              )
            """
        )
        orphan_news = cur.fetchall()

        print(
            f"[Obsidian] 导出宏观笔记数={len(macros)}（{_macro_export_note}）"
        )

        def _coerce_eid_row(row: dict, key: str = "event_id") -> None:
            v = row.get(key)
            if v is None:
                return
            try:
                row[key] = int(v)
            except (TypeError, ValueError):
                pass

        for row in micros:
            _coerce_eid_row(row)
        for row in mapping:
            _coerce_eid_row(row)
        for row in news:
            _coerce_eid_row(row)

        return macros, micros, mapping, news, orphan_news
    finally:
        conn.close()


def _export_front_artifacts(vault_dir: Path) -> None:
    """将宏观 JSON / 若存在的前端 graph_data 复制到库侧，便于对接 web-graph。"""
    export_dir = vault_dir / "_front_export"
    export_dir.mkdir(parents=True, exist_ok=True)
    macro_json = BASE_DIR / "data" / "macro_events" / "macro_events.json"
    if macro_json.is_file():
        dest = export_dir / "macro_events.json"
        shutil.copy2(macro_json, dest)
        print(f"[Export] {dest}")
    graph_json = BASE_DIR / "outputs" / "graph_data.json"
    if graph_json.is_file():
        dest = export_dir / "graph_data.json"
        shutil.copy2(graph_json, dest)
        print(f"[Export] {dest}（前端/检索可选用）")
    else:
        print(f"[Export] 无 {graph_json}，跳过图谱 JSON 复制（若需请先产出 graph_data.json）")


def run_sync_v4(
    output_dir: Optional[str | Path] = None,
    *,
    export_front_artifacts: bool = True,
    clear_vault: bool = False,
) -> None:
    vault = Path(output_dir).expanduser().resolve() if output_dir else DEFAULT_VAULT_DIR
    if os.getenv("OBSIDIAN_SYNC_TITLES_TO_DB", "1").strip().lower() not in ("0", "false", "no"):
        try:
            sync_titles_from_obsidian_to_db(vault)
        except Exception as e:
            print(f"[Obsidian] 标题反向同步失败（不中断导出）: {type(e).__name__}: {e}")
    micro_dir, rel_micro_prefix = _micro_dir_and_prefix(vault)
    macro_dir = vault / "MacroEvents"
    articles_dir = vault / "Articles"

    if clear_vault:
        to_clear = {micro_dir.resolve()}
        # 遗留：旧版曾写入 Events/Micro，同步前一并删掉避免占位残留
        leg = (vault / "Events" / "Micro").resolve()
        to_clear.add(leg)
        for d in sorted(to_clear, key=str):
            if d.exists():
                shutil.rmtree(d)
                try:
                    rel_show = d.relative_to(vault)
                except ValueError:
                    rel_show = d
                print(f"[Obsidian] --clear-vault：已删除 {rel_show}")
        # 若顶层 Events/ 已空（仅曾含 Events/Micro），一并移除
        ev_root = vault / "Events"
        if ev_root.is_dir():
            try:
                ev_root.rmdir()
                print("[Obsidian] --clear-vault：已删除空目录 Events/")
            except OSError:
                pass

    print(f"[Obsidian] 正在生成 v4 知识库 → {vault}（Micro 路径前缀「{rel_micro_prefix}」）")
    macros, micros, mapping, news, orphan_news = fetch_all_data()

    min_micro_art = _obsidian_micro_min_articles()
    micros_page = [
        m for m in micros if int(m.get("article_count") or 0) >= min_micro_art
    ]
    micro_eids_page = {m["event_id"] for m in micros_page}
    n_pruned_micro = len(micros) - len(micros_page)
    print(
        f"[Obsidian] 微事件笔记阈值 article_count>={min_micro_art}，"
        f"生成笔记 {len(micros_page)} 个，跳过单篇/不足 {n_pruned_micro} 个"
    )
    micro_by_eid = {m["event_id"]: m for m in micros}

    macro_ids_keep_all = {m["storyline_id"] for m in macros}
    t_clean = time.perf_counter()
    n_rm = 0
    for d in [macro_dir, articles_dir, micro_dir]:
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.md"):
            f.unlink()
            n_rm += 1
    if n_rm:
        print(
            f"[Obsidian] 已清理旧 Markdown {n_rm} 个，耗时 {time.perf_counter() - t_clean:.1f}s"
        )

    # 一微对多宏观：先收集 storyline_micro_map 中全部 (event_id, storyline_id) 边
    micro_to_macros_all: dict[int, list[int]] = defaultdict(list)
    seen_map_pair: set[tuple[int, int]] = set()
    for m in mapping:
        try:
            eid = int(m["event_id"])
            sid = int(m["storyline_id"])
        except (TypeError, ValueError):
            continue
        if sid not in macro_ids_keep_all:
            continue
        if (eid, sid) in seen_map_pair:
            continue
        seen_map_pair.add((eid, sid))
        micro_to_macros_all[eid].append(sid)

    for mu in micros:
        try:
            eid = int(mu["event_id"])
        except (TypeError, ValueError):
            continue
        raw = mu.get("macro_storyline_id")
        if raw is None:
            continue
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            continue
        if sid not in macro_ids_keep_all:
            continue
        if sid not in micro_to_macros_all[eid]:
            micro_to_macros_all[eid].append(sid)

    # Hollow Macro 过滤：仅导出至少包含 N 个“可链接 Micro（将生成独立 Micro 笔记）”的宏观
    min_linked = _obsidian_macro_min_linked_micros()
    macro_linked_counts: dict[int, int] = defaultdict(int)
    for eid in micro_eids_page:
        for sid in micro_to_macros_all.get(eid, []):
            macro_linked_counts[sid] += 1
    macros_filtered = [
        m
        for m in macros
        if macro_linked_counts.get(int(m["storyline_id"]), 0) >= min_linked
    ]
    n_hollow_skipped = len(macros) - len(macros_filtered)
    if n_hollow_skipped > 0:
        print(
            f"[Obsidian] Hollow Macro 过滤：要求 linked_micros>={min_linked}，"
            f"跳过 {n_hollow_skipped} 个空心宏观（仅展示层，不改 DB）"
        )
    macros = macros_filtered
    macro_ids_keep = {m["storyline_id"] for m in macros}

    # 过滤后的一微对多宏观边（供 Micro 父链接 / Macro 子链接）
    micro_to_macros: dict[int, list[int]] = defaultdict(list)
    for eid, sids in micro_to_macros_all.items():
        kept = [sid for sid in sids if sid in macro_ids_keep]
        if kept:
            micro_to_macros[eid] = kept

    n_multi_parent = sum(1 for _eid, sids in micro_to_macros.items() if len(sids) > 1)
    if n_multi_parent:
        print(
            f"[Obsidian] 多重宏观归属：{n_multi_parent} 个微事件在 storyline_micro_map 中"
            f"有多条父故事线（Obsidian 导出已全部保留为链接）"
        )

    macro_titles = {m["storyline_id"]: m["title"] for m in macros}

    micro_filenames: dict = {}
    for m in micros_page:
        eid = m["event_id"]
        safe_title = _safe_filename(m["title"])
        micro_filenames[eid] = f"E{eid}_{safe_title}.md"

    if _obsidian_micro_stub_links():
        for mu in micros:
            eid = mu["event_id"]
            if eid in micro_filenames:
                continue
            if not any(s in macro_titles for s in micro_to_macros.get(eid, [])):
                continue
            safe_title = _safe_filename(mu.get("title"))
            micro_filenames[eid] = f"E{eid}_{safe_title}.md"

    micro_news = defaultdict(list)
    for n in news:
        micro_news[n["event_id"]].append(n)

    t_fb = time.perf_counter()
    use_all_micros_fb = os.getenv("OBSIDIAN_FALLBACK_ALL_MICROS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    micros_fb = micros if use_all_micros_fb else micros_page
    fallback_attached = _attach_news_by_title_date_fallback(
        micros_fb, orphan_news, micro_news
    )
    fb_scope = f"全部微事件 {len(micros_fb)}" if use_all_micros_fb else f"将导出 {len(micros_page)}"
    print(
        f"[Obsidian] 孤儿新闻兜底匹配（{fb_scope} 个）"
        f" 耗时 {time.perf_counter() - t_fb:.1f}s，新挂接 {fallback_attached} 条"
        f"（跳过兜底：OBSIDIAN_SKIP_ORPHAN_FALLBACK=1；"
        f"对 4151 个全量重试：OBSIDIAN_FALLBACK_ALL_MICROS=1）"
    )

    dup_removed = _dedupe_micro_news(micro_news)
    if dup_removed:
        print(f"[Obsidian] 去重重复成员文章={dup_removed}")

    n_article_rows = sum(len(items) for items in micro_news.values())
    print(
        f"[Obsidian] 正在写入 Articles（约 {n_article_rows} 条成员新闻），"
        "Windows 下大量小文件可能需数分钟，请稍候…"
    )
    t_art = time.perf_counter()
    n_written_art = 0
    log_every = max(2000, n_article_rows // 20 or 1)

    for eid, items in micro_news.items():
        micro_name = micro_filenames.get(eid)
        if eid in micro_filenames and micro_name:
            cluster_val = f'"[[{rel_micro_prefix}/{micro_name[:-3]}]]"'
            body_micro = f"**所属微事件**：[[{rel_micro_prefix}/{micro_name[:-3]}]]"
        else:
            mt = micro_by_eid.get(eid, {})
            label = (mt.get("title") or f"微簇{eid}").replace('"', "'")
            cluster_val = f'"(单篇·无聚类笔记) {label}"'
            body_micro = f"**所属微事件**：（仅 1 篇或未达阈值，无独立 Micro 页）{label}"
        for n in items:
            _ci = _yaml_float(n.get("china_related_index"))
            _ci_out = round(_ci, 4) if _ci is not None else 0.0
            article_md = f"""---
{_yaml_title_line(n.get('title'))}
pub_date: {format_pub_date_for_yaml(n.get('pub_time')) or "—"}
cluster: {cluster_val}
sentiment: {n.get('sentiment_analysis', '中性')}
is_china: {n.get('is_china_related', False)}
china_index: {_ci_out}
topic: {n.get('topic_classification', '其他')}
---
# {n.get('title') or ''}

{body_micro}

{n.get('abstract') or '暂无摘要'}

[原文链接]({n.get('url') or ''})
"""
            (articles_dir / f"{n['news_id']}.md").write_text(article_md, encoding="utf-8")
            n_written_art += 1
            if n_written_art % log_every == 0 or n_written_art == n_article_rows:
                print(
                    f"[Obsidian] Articles 进度 {n_written_art}/{n_article_rows} "
                    f"（已过 {time.perf_counter() - t_art:.1f}s）"
                )

    print(f"[Obsidian] Articles 写入完成，总耗时 {time.perf_counter() - t_art:.1f}s")

    for m in micros_page:
        eid = m["event_id"]
        fname = micro_filenames[eid]
        parent_sids = micro_to_macros.get(eid, [])
        macro_line = _macro_parent_body_block(parent_sids, macro_titles)

        lines = [
            "---",
            "tags:",
            "  - EventCluster",
            _yaml_title_line(m.get("title")),
            f"event_id: {eid}",
        ]
        _append_parent_storylines_frontmatter(lines, parent_sids)
        _append_intel_frontmatter(lines, m)
        lines.extend(
            [
                "---",
                "",
                f"# {m['title']}",
                macro_line,
                f"\n> 包含 {m.get('article_count', 0)} 篇核心新闻",
                "",
                _depth_micro_section(m, micro_news.get(eid, [])),
                "## 关联文章",
            ]
        )
        for n in micro_news.get(eid, []):
            lines.append(f"- [[Articles/{n['news_id']}|{n.get('title') or n['news_id']}]]")
        (micro_dir / fname).write_text("\n".join(lines), encoding="utf-8")

    stub_eids = set(micro_filenames.keys()) - micro_eids_page
    if stub_eids:
        min_ma = _obsidian_micro_min_articles()
        for eid in sorted(stub_eids):
            mstub = micro_by_eid.get(eid)
            if not mstub:
                continue
            fname = micro_filenames[eid]
            parent_sids = micro_to_macros.get(eid, [])
            macro_line = _macro_parent_body_block(parent_sids, macro_titles)
            lines = [
                "---",
                "tags:",
                "  - EventCluster",
                "  - MicroStub",
                _yaml_title_line(mstub.get("title")),
                f"event_id: {eid}",
                "micro_stub: true",
            ]
            _append_parent_storylines_frontmatter(lines, parent_sids)
            _append_intel_frontmatter(lines, mstub)
            lines.extend(
                [
                    "---",
                    "",
                    f"# {mstub['title']}",
                    macro_line,
                    f"\n> 占位笔记（未达 {min_ma} 篇阈值，用于与宏观笔记双向链接）。"
                    f"当前 article_count={mstub.get('article_count', 0)}。",
                    "",
                    _depth_micro_section(mstub, micro_news.get(eid, [])),
                    "## 关联文章",
                ]
            )
            for n in micro_news.get(eid, []):
                lines.append(f"- [[Articles/{n['news_id']}|{n.get('title') or n['news_id']}]]")
            (micro_dir / fname).write_text("\n".join(lines), encoding="utf-8")
        print(
            f"[Obsidian] 已写入占位 Micro 笔记 {len(stub_eids)} 个（OBSIDIAN_MICRO_STUB_LINKS）"
        )

    for m in macros:
        sid = m['storyline_id']
        safe_title = _safe_filename(m['title'])
        subs = [mi for mi in micros if sid in micro_to_macros.get(mi["event_id"], [])]
        subs_rank = sorted(
            subs,
            key=lambda x: (-int(x.get("article_count") or 0), x.get("start_date") or ""),
        )
        desc_txt = (m.get("description") or "").strip()
        content = [
            "---",
            "tags:",
            "  - MacroEvent",
            _yaml_title_line(m.get("title")),
            f"storyline_id: {sid}",
        ]
        _append_intel_frontmatter(content, m)
        content.extend(
            [
                "---",
                "",
                f"# 故事线：{m['title']}",
                f"- **时间跨度**: {format_pub_date_for_yaml(m.get('start_date')) or m.get('start_date')} "
                f"～ {format_pub_date_for_yaml(m.get('end_date')) or m.get('end_date')}",
                f"- **微事件数**: {m['micro_event_count']}",
                f"- **文章总量**: {m['article_count']}",
                "",
                _depth_macro_section(m, subs_rank, micro_news),
            ]
        )
        if desc_txt:
            content.extend(["## 综述", "", desc_txt, ""])
        content.extend(["## 子事件", ""])
        top_vis = 10
        if len(subs_rank) > 20:
            head = subs_rank[:top_vis]
            tail = subs_rank[top_vis:]
            for se in head:
                content.append(_fmt_macro_sub_line(se, micro_filenames, rel_micro_prefix))
            content.append("")
            content.append("<details><summary>更多子事件（点击展开）</summary>")
            content.append("")
            for se in tail:
                content.append(_fmt_macro_sub_line(se, micro_filenames, rel_micro_prefix))
            content.append("")
            content.append("</details>")
        else:
            for se in subs_rank:
                content.append(_fmt_macro_sub_line(se, micro_filenames, rel_micro_prefix))
        (macro_dir / f"{safe_title}.md").write_text("\n".join(content), encoding="utf-8")

    n_art = sum(1 for _ in articles_dir.glob("*.md"))
    print(
        f"[Obsidian] 同步完成: Macro={len(macros)} "
        f"Micro笔记={len(micros_page)} Articles={n_art}"
    )
    if export_front_artifacts:
        _export_front_artifacts(vault)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Obsidian v4 知识库同步")
    ap.add_argument(
        "--output-dir",
        type=str,
        default="",
        help="Obsidian 库根目录，默认 <仓库根>/obsidian_vault",
    )
    ap.add_argument("--no-export", action="store_true", help="不复制 _front_export 下的 JSON")
    ap.add_argument(
        "--clear-vault",
        action="store_true",
        help="同步前删除当前 Micro 目录（及另一布局下的遗留目录），避免旧占位笔记残留",
    )
    ns = ap.parse_args()
    run_sync_v4(
        output_dir=ns.output_dir or None,
        export_front_artifacts=not ns.no_export,
        clear_vault=ns.clear_vault,
    )
