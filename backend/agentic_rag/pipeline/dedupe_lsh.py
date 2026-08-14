"""
Stage 1a 前置：MinHash + LSH 近重复检测（datasketch）。

- 文本：title + abstract（与涉华指纹无关，仅去重）
- 分词：CJK 字符 3-gram；英文/数字为词级 3-gram（空格分词）
- 持久化：data/dedupe/minhash_lsh.pkl，原子写入（.tmp + os.replace）
"""
from __future__ import annotations

import os
import pickle
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Tuple

if TYPE_CHECKING:
    from datasketch import MinHash, MinHashLSH

_DEFAULT_NUM_PERM = 128


def _default_lsh_threshold() -> float:
    try:
        from config.settings import FrozenDefaults

        return float(FrozenDefaults.LSH_JACCARD)
    except Exception:
        return 0.85
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def _dedupe_text(row: dict) -> str:
    t = (row.get("title") or "").strip()
    a = (row.get("abstract") or "").strip()
    return f"{t}\n{a}".strip()


def fingerprint_tokens(text: str) -> List[str]:
    """CJK：字符 3-gram；英文：词 3-gram。微秒级，无重 NLP。"""
    if not text:
        return []
    out: List[str] = []
    low = text.lower()
    words = _WORD_RE.findall(low)
    if len(words) >= 3:
        for i in range(len(words) - 2):
            out.append(" ".join(words[i : i + 3]))
    elif words:
        w = " ".join(words)
        if len(w) >= 3:
            for i in range(len(w) - 2):
                out.append(w[i : i + 3])
        else:
            out.append(w)
    for m in _CJK_RE.finditer(text):
        seg = m.group()
        if len(seg) >= 3:
            for i in range(len(seg) - 2):
                out.append(seg[i : i + 3])
        elif seg:
            out.append(seg)
    if not out:
        out.append("__empty__")
    return out


def _make_minhash(tokens: List[str]) -> "MinHash":
    from datasketch import MinHash

    num_perm = int(os.getenv("DEDUPE_LSH_NUM_PERM", str(_DEFAULT_NUM_PERM)))
    mh = MinHash(num_perm=num_perm)
    for tok in tokens:
        mh.update(tok.encode("utf-8"))
    return mh


def minhash_for_row(row: dict) -> "MinHash":
    return _make_minhash(fingerprint_tokens(_dedupe_text(row)))


def dedupe_enabled() -> bool:
    return os.getenv("DEDUPE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def lsh_pickle_path() -> Path:
    base = Path(__file__).resolve().parent.parent / "data" / "dedupe"
    return Path(os.getenv("DEDUPE_LSH_PICKLE", str(base / "minhash_lsh.pkl")))


def load_lsh_index() -> "MinHashLSH":
    from datasketch import MinHashLSH

    path = lsh_pickle_path()
    thr = float(os.getenv("DEDUPE_LSH_THRESHOLD", str(_default_lsh_threshold())))
    num_perm = int(os.getenv("DEDUPE_LSH_NUM_PERM", str(_DEFAULT_NUM_PERM)))
    if path.is_file():
        try:
            with open(path, "rb") as f:
                obj = pickle.load(f)
            if isinstance(obj, MinHashLSH):
                return obj
        except Exception:
            pass
    return MinHashLSH(threshold=thr, num_perm=num_perm)


def save_lsh_index_atomic(lsh: "MinHashLSH") -> None:
    path = lsh_pickle_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(lsh, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)


def _batch_jaccard_min() -> float:
    try:
        return float(os.getenv("DEDUPE_BATCH_JACCARD", str(_default_lsh_threshold())))
    except ValueError:
        return _default_lsh_threshold()


def classify_batch(
    lsh: "MinHashLSH",
    rows: List[dict],
) -> Tuple[List[dict], List[Tuple[dict, int]], List[Any]]:
    """
    返回：
      novel_rows — 需走 BGE 的新稿（按 id 升序）
      dup_pairs — (row, canonical_news_id)，canonical 取自 LSH 或本批更早的 novel
      novel_minhashes — 与 novel_rows 一一对应的 MinHash（用于写库成功后 insert）
    """
    jaccard_min = _batch_jaccard_min()
    sorted_rows = sorted(rows, key=lambda r: int(r["id"]))
    novel_rows: List[dict] = []
    novel_minhashes: List[Any] = []
    dup_pairs: List[Tuple[dict, int]] = []
    batch_minhashes: List[Tuple[int, Any]] = []

    for row in sorted_rows:
        rid = int(row["id"])
        mh = minhash_for_row(row)
        canon: int | None = None
        for k in lsh.query(mh):
            oid = int(k)
            if oid != rid:
                canon = oid if canon is None else min(canon, oid)
        if canon is None:
            for oid, omh in batch_minhashes:
                try:
                    if mh.jaccard(omh) >= jaccard_min:
                        canon = oid if canon is None else min(canon, oid)
                except Exception:
                    continue
        if canon is not None:
            dup_pairs.append((row, canon))
        else:
            novel_rows.append(row)
            novel_minhashes.append(mh)
            batch_minhashes.append((rid, mh))

    return novel_rows, dup_pairs, novel_minhashes


def insert_novels_into_lsh(
    lsh: "MinHashLSH",
    novel_rows: List[dict],
    novel_minhashes: List[Any],
) -> None:
    for row, mh in zip(novel_rows, novel_minhashes):
        lsh.insert(str(int(row["id"])), mh)
