"""
由原始数据构建 3D 力导向图所需的 nodes/links 与 clusters_meta（与前端约定字段一致）。

可单独调用 build_graph(...) 生成拓扑 JSON，无需访问数据库。
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config.settings import FrozenDefaults as FD


def compute_umap_3d(embeddings, umap_model_path: Path):
    """用已训练的 UMAP 模型把 50D 向量降到 3D 用于图谱坐标"""
    import pickle

    import numpy as np

    try:
        with open(umap_model_path, "rb") as f:
            umap_model = pickle.load(f)  # noqa: F841 — 与历史脚本一致，后续仍走全新 UMAP 拟合
        print(f"[UMAP] Reducing {embeddings.shape[0]}x{embeddings.shape[1]}D -> 3D ...")
        import umap

        reducer_3d = umap.UMAP(
            n_components=3,
            metric="cosine",
            random_state=42,
            n_jobs=1,
            verbose=False,
        )
        coords = reducer_3d.fit_transform(embeddings)
        for i in range(3):
            mn, mx = coords[:, i].min(), coords[:, i].max()
            if mx > mn:
                coords[:, i] = (coords[:, i] - mn) / (mx - mn) * 2000 - 1000
        print(f"[UMAP] Done: {coords.shape}")
        return coords
    except Exception as e:
        print(f"[UMAP] Warning: 3D reduction failed ({e}), using random coords")
        return None


def compute_cluster_centroids(embeddings, cluster_ids) -> Dict[int, list]:
    """计算每个簇的质心向量（50D），返回 {cluster_id: centroid_vec}"""
    import numpy as np
    from collections import defaultdict as dd

    groups: dict = dd(list)
    for emb, cid in zip(embeddings, cluster_ids):
        if cid >= 0:
            groups[cid].append(emb)
    centroids = {}
    for cid, vecs in groups.items():
        c = np.mean(np.stack(vecs), axis=0)
        norm = np.linalg.norm(c)
        centroids[cid] = (c / norm if norm > 0 else c).tolist()
    print(f"[Centroids] Computed {len(centroids)} cluster centroids")
    return centroids


def build_semantic_cluster_links(
    centroids: Dict[int, list], top_k: int = 3, min_sim: float = 0.3
) -> List[dict]:
    """基于质心余弦相似度，为每个簇连接最相似的 top_k 个邻居簇"""
    import numpy as np

    links = []
    cids = sorted(centroids.keys())
    if len(cids) < 2:
        return links
    mat = np.stack([centroids[c] for c in cids])
    sim = mat @ mat.T
    seen = set()
    for i, cid_i in enumerate(cids):
        sims_i = sim[i].copy()
        sims_i[i] = -1
        top = np.argsort(sims_i)[::-1][:top_k]
        for j in top:
            s = float(sims_i[j])
            if s < min_sim:
                continue
            pair = tuple(sorted([cid_i, cids[j]]))
            if pair not in seen:
                seen.add(pair)
                links.append(
                    {
                        "source": f"Cluster_{cid_i}",
                        "target": f"Cluster_{cids[j]}",
                        "similarity": round(s, 3),
                    }
                )
    print(f"[SemanticLinks] Built {len(links)} cluster-cluster semantic links")
    return links


def compute_umap_3d_from_embeddings(
    news_ids: List[int],
    embeddings,
    cluster_ids: List[int],
) -> Dict[int, tuple]:
    import time

    import numpy as np

    try:
        import umap as umap_lib
    except ImportError:
        print("[UMAP] umap-learn not installed, falling back to spherical coords")
        return {}

    print(f"[UMAP] Reducing {embeddings.shape[0]}x{embeddings.shape[1]}D -> 3D ...")
    t0 = time.perf_counter()
    reducer = umap_lib.UMAP(
        n_components=3,
        n_neighbors=15,
        min_dist=0.05,
        metric="cosine",
        low_memory=True,
        random_state=42,
        verbose=True,
    )
    coords = reducer.fit_transform(embeddings).astype("float32")
    elapsed = time.perf_counter() - t0
    print(f"[UMAP] Done in {elapsed:.1f}s, shape={coords.shape}")

    for i in range(3):
        mn, mx = coords[:, i].min(), coords[:, i].max()
        if mx > mn:
            coords[:, i] = (coords[:, i] - mn) / (mx - mn) * 2000 - 1000

    coord_map: Dict[int, tuple] = {}
    for i, nid in enumerate(news_ids):
        coord_map[nid] = (
            round(float(coords[i, 0]), 2),
            round(float(coords[i, 1]), 2),
            round(float(coords[i, 2]), 2),
        )
    print(f"[UMAP] coord_map built for {len(coord_map)} news nodes")
    return coord_map


def merge_storyline_titles(macro_events: Dict[int, dict], pg_macro: Dict[int, str]) -> Dict[int, str]:
    """优先 JSON 内标题，其次 PG macro_storylines.title。"""
    out: Dict[int, str] = {}
    for sid, ev in macro_events.items():
        t = (ev.get("macro_title") or "").strip()
        if t:
            out[int(sid)] = t
    for sid, t in pg_macro.items():
        if not t:
            continue
        if sid not in out or not out[sid]:
            out[sid] = t
    return out


def _v_norm3(v: List[float]) -> List[float]:
    import math as _m

    l = _m.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return [v[0] / l, v[1] / l, v[2] / l]


def _v_add(a: List[float], b: List[float]) -> List[float]:
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def _v_scale(v: List[float], s: float) -> List[float]:
    return [v[0] * s, v[1] * s, v[2] * s]


def _unit_from_int(seed: int, salt: int = 0) -> List[float]:
    import math as _m

    x = _m.sin((seed + salt) * 12.9898 + salt * 7.13) * 43758.5453
    x = x - _m.floor(x)
    y = _m.sin((seed * 3 + salt) * 78.233) * 23421.413
    y = y - _m.floor(y)
    z = _m.sin((seed * 7 + salt * 11) * 45.164) * 31415.926
    z = z - _m.floor(z)
    return _v_norm3([x * 2.0 - 1.0, y * 2.0 - 1.0, z * 2.0 - 1.0])


def _fibonacci_sphere(i: int, n: int, radius: float) -> List[float]:
    import math as _m

    if n <= 1:
        return [0.0, 0.0, radius]
    golden = _m.pi * (3.0 - _m.sqrt(5.0))
    t = golden * i
    y = 1 - (i / max(n - 1, 1)) * 2
    rr = _m.sqrt(max(0.0, 1.0 - y * y))
    return [radius * rr * _m.cos(t), radius * rr * _m.sin(t), radius * y]


def _is_ghost_news(cid: Optional[int], ac: int, st_id: Optional[int]) -> bool:
    """与前端 isGhostNode 一致：无簇 / 单篇 / 无宏观挂载。"""
    if cid is None or int(cid) < 0:
        return True
    if ac <= 1:
        return True
    if st_id is None:
        return True
    return False


def build_graph(
    news_cluster_map,
    title_map,
    cluster_meta,
    coord_map=None,
    semantic_links=None,
    macro_events: Optional[Dict[int, dict]] = None,
    fine_to_macro: Optional[Dict[int, int]] = None,
    storyline_titles: Optional[Dict[int, str]] = None,
    micro_titles: Optional[Dict[int, str]] = None,
):
    """coord_map 已废弃（保留参数兼容）；布局由前端力导向 + 层级种子坐标完成。"""
    import math as _math

    macro_events = macro_events or {}
    fine_to_macro = fine_to_macro or {}
    storyline_titles = storyline_titles or {}
    micro_titles = micro_titles or {}

    cluster_counts: Dict[int, int] = defaultdict(int)
    for cid in news_cluster_map.values():
        if cid >= 0:
            cluster_counts[cid] += 1
    for cid, meta in cluster_meta.items():
        if meta["article_count"] > 0:
            cluster_counts[cid] = meta["article_count"]

    cluster_news_ids = defaultdict(list)
    for nid, cid in news_cluster_map.items():
        if cid >= 0:
            cluster_news_ids[cid].append(nid)

    all_cids = sorted(cluster_counts.keys())
    clusters_meta: Dict[str, dict] = {}
    for cid in all_cids:
        count = cluster_counts[cid]
        clusters_meta[f"Cluster_{cid}"] = {
            "cluster_id": cid,
            "article_count": count,
            "group": cid % 20,
            "news_ids": cluster_news_ids[cid],
        }

    sid_list = sorted(macro_events.keys())
    n_m = len(sid_list)
    macro_pos: Dict[int, List[float]] = {}
    for idx, sid in enumerate(sid_list):
        macro_pos[sid] = _fibonacci_sphere(idx, max(n_m, 1), FD.MACRO_SPHERE_R)

    orphan_clusters = [c for c in all_cids if fine_to_macro.get(c) is None]
    cluster_pos: Dict[int, List[float]] = {}
    for j, cid in enumerate(orphan_clusters):
        cluster_pos[cid] = _fibonacci_sphere(j, max(len(orphan_clusters), 1), FD.MACRO_SPHERE_R * 1.35)

    for cid in all_cids:
        if cid in cluster_pos:
            continue
        sid = fine_to_macro.get(cid)
        if sid is not None and sid in macro_pos:
            off = _v_scale(_unit_from_int(cid, salt=sid), FD.CLUSTER_ARM)
            cluster_pos[cid] = _v_add(macro_pos[sid], off)
        else:
            cluster_pos[cid] = _fibonacci_sphere(hash(cid) % 512, 512, FD.MACRO_SPHERE_R * 1.2)

    nodes_out: List[dict] = []
    links: List[dict] = []
    link_seen = set()

    def _add_link(src: str, tgt: str) -> None:
        key = (src, tgt)
        if key in link_seen:
            return
        link_seen.add(key)
        links.append({"source": src, "target": tgt})

    for sid in sid_list:
        ev = macro_events[sid]
        fids = ev.get("fine_cluster_ids", [])
        mt = (storyline_titles.get(sid) or ev.get("macro_title") or "").strip()
        window = ev.get("window", str(sid))
        ac_m = int(ev.get("article_count", 0))
        mname = mt or window or f"宏观故事线 {sid}"
        pv = macro_pos[sid]
        mval = int(FD.MACRO_VAL_BASE + min(72, _math.sqrt(float(max(ac_m, 1))) * 3.2))
        nodes_out.append(
            {
                "id": f"Macro_{sid}",
                "name": mname,
                "type": "macro",
                "val": mval,
                "group": sid % 20,
                "storyline_id": sid,
                "macro_id": sid,
                "macro_title": mt,
                "fine_cluster_ids": [int(x) for x in fids],
                "article_count": ac_m,
                "fine_cluster_count": int(ev.get("fine_cluster_count", len(fids))),
                "timeline_start": ev.get("timeline_start", "") or "",
                "timeline_end": ev.get("timeline_end", "") or "",
                "x": round(pv[0], 2),
                "y": round(pv[1], 2),
                "z": round(pv[2], 2),
            }
        )
        for fid in fids:
            fid = int(fid)
            if fid in cluster_counts:
                _add_link(f"Cluster_{fid}", f"Macro_{sid}")

    for cid in all_cids:
        st = fine_to_macro.get(cid)
        ccount = cluster_counts[cid]
        mic_name = (micro_titles.get(cid) or "").strip() or f"微簇{cid}"
        cv = int(FD.MICRO_VAL_BASE + min(8, _math.log(max(ccount, 1) + 1.0) * 2.8))
        pv = cluster_pos[cid]
        nodes_out.append(
            {
                "id": f"Cluster_{cid}",
                "name": mic_name,
                "type": "micro",
                "val": cv,
                "group": cid % 20,
                "cluster_id": cid,
                "storyline_id": st,
                "macro_id": st,
                "article_count": ccount,
                "micro_title": mic_name,
                "x": round(pv[0], 2),
                "y": round(pv[1], 2),
                "z": round(pv[2], 2),
            }
        )

    ghost_i = 0
    n_ghost_est = max(
        1,
        sum(
            1
            for nid, cid in news_cluster_map.items()
            if _is_ghost_news(
                cid if cid >= 0 else None,
                int(cluster_counts.get(cid, 0)) if cid >= 0 else 0,
                fine_to_macro.get(cid) if cid >= 0 else None,
            )
        ),
    )

    for nid, cid in news_cluster_map.items():
        title = title_map.get(nid, f"News {nid}")
        ac = int(cluster_counts.get(cid, 0)) if cid >= 0 else 0
        st_id: Optional[int] = None
        if cid >= 0:
            st_id = fine_to_macro.get(cid)
        mtitle = ""
        mic_title = ""
        if cid >= 0:
            mic_title = (micro_titles.get(cid) or "").strip() or f"微簇{cid}"
        if st_id is not None:
            mtitle = (storyline_titles.get(st_id) or "").strip()
        ghost = _is_ghost_news(
            cid if cid >= 0 else None,
            ac,
            st_id,
        )
        if cid >= 0 and (not ghost):
            spr = FD.NEWS_JITTER_BASE + min(55.0, _math.sqrt(float(ac)) * 6.0)
            bv = cluster_pos.get(cid, [0.0, 0.0, 0.0])
            ju = _v_scale(_unit_from_int(nid, salt=cid + 777), spr)
            px, py, pz = _v_add(bv, ju)
        else:
            px, py, pz = _fibonacci_sphere(ghost_i, n_ghost_est, FD.GHOST_SPHERE_R)
            ghost_i += 1

        node = {
            "id": f"News_{nid}",
            "name": title,
            "group": cid % 20 if cid >= 0 else 19,
            "cluster_id": cid if cid >= 0 else None,
            "cluster_article_count": ac,
            "val": FD.NEWS_VAL,
            "type": "news",
            "storyline_id": st_id,
            "macro_id": st_id,
            "macro_title": mtitle,
            "micro_title": mic_title,
            "x": round(px, 2),
            "y": round(py, 2),
            "z": round(pz, 2),
        }
        nodes_out.append(node)
        if not ghost and cid >= 0:
            _add_link(f"News_{nid}", f"Cluster_{cid}")

    for sid in sid_list:
        ev = macro_events[sid]
        st = int(ev.get("storyline_id", sid))
        mt = (storyline_titles.get(st) or ev.get("macro_title") or "").strip()
        fids = ev.get("fine_cluster_ids", [])
        me = {
            "storyline_id": st,
            "macro_id": st,
            "macro_title": mt,
            "window": str(ev.get("window", str(st))),
            "article_count": ev["article_count"],
            "fine_cluster_ids": [int(x) for x in fids],
            "fine_cluster_count": ev["fine_cluster_count"],
            "timeline_start": ev.get("timeline_start", "") or "",
            "timeline_end": ev.get("timeline_end", "") or "",
            "group": st % 20,
        }
        pv = macro_pos.get(st)
        if pv:
            me["cx"] = round(pv[0], 2)
            me["cy"] = round(pv[1], 2)
            me["cz"] = round(pv[2], 2)
        clusters_meta[f"Macro_{st}"] = me

    for cid in all_cids:
        cm = clusters_meta[f"Cluster_{cid}"]
        pv = cluster_pos[cid]
        cm["cx"] = round(pv[0], 2)
        cm["cy"] = round(pv[1], 2)
        cm["cz"] = round(pv[2], 2)
        ms = fine_to_macro.get(cid)
        if ms is not None and ms in macro_pos:
            dx = [cluster_pos[cid][i] - macro_pos[ms][i] for i in range(3)]
        else:
            dx = [80.0, 80.0, 80.0]
        cm["radius"] = round(_math.sqrt(dx[0] ** 2 + dx[1] ** 2 + dx[2] ** 2) * 0.5 + 40.0, 2)

    nm = sum(1 for n in nodes_out if n.get("type") == "macro")
    nc = sum(1 for n in nodes_out if n.get("type") == "micro")
    nn = sum(1 for n in nodes_out if n.get("type") == "news")
    print(f"[Graph] galaxy nodes macro={nm} micro={nc} news={nn} links={len(links)} meta={len(clusters_meta)}")
    return {"nodes": nodes_out, "links": links}, clusters_meta


# 兼容旧模块名
_merge_storyline_titles = merge_storyline_titles
