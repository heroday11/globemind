"""V2 Embedder: pure BGE-M3 only.

Design goals:
- Single provider only: HuggingFace sentence-transformers `BAAI/bge-m3`
- No Ollama / no Hash fallback / no UMAP wrapping
- Compatible with domestic mirror via HF_ENDPOINT
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional, Protocol, Union


def _bootstrap_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        here = Path(__file__).resolve().parent.parent
        root = here.parent
        for p in (here / ".env", root / ".env"):
            if p.is_file():
                load_dotenv(p, override=False)
    except ImportError:
        pass


_bootstrap_dotenv()


def _apply_hf_endpoint_defaults() -> None:
    """HF_HUB_OFFLINE=1 时不强制镜像，便于仅用本地模型快照。"""
    off = (os.getenv("HF_HUB_OFFLINE") or "").strip().lower()
    if off in ("1", "true", "yes", "on"):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        return
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_OFFLINE", "0")


_apply_hf_endpoint_defaults()


def _apply_models_hf_env() -> None:
    """将 HF 缓存指向 ontology_and_trust.yaml models.hf_cache_root，减轻系统盘占用。"""
    try:
        from config.settings import get_models_config

        cfg = get_models_config()
        hf_cache = (cfg.get("hf_cache_root") or "").strip()
        if hf_cache:
            p = Path(hf_cache.replace("/", os.sep))
            os.environ.setdefault("HF_HOME", str(p))
            os.environ.setdefault("HF_HUB_CACHE", str(p / "hub"))
    except Exception:
        pass


_apply_models_hf_env()

import numpy as np


class EmbedderProtocol(Protocol):
    @property
    def dim(self) -> int: ...

    def encode(self, texts: List[str]) -> np.ndarray: ...


def _parse_tei_embed_json(data: Any) -> np.ndarray:
    """Parse TEI POST /embed JSON body into (n, dim) float32."""
    if isinstance(data, dict):
        if "embeddings" in data:
            data = data["embeddings"]
        else:
            raise ValueError(
                "TEI /embed returned dict without 'embeddings'; "
                f"keys={list(data.keys())[:12]}"
            )
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim != 2:
        raise ValueError(f"TEI /embed array ndim={arr.ndim}, expected 2")
    return arr


def _l2_normalize_rows(vecs: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return vecs / norms


class BgeM3Embedder:
    """Pure BGE-M3 embedder (sentence-transformers)."""

    MODEL_NAME = "BAAI/bge-m3"

    @staticmethod
    def _resolve_model_id_or_path() -> str:
        """优先使用本地已迁移的 snapshot（models.local_model_root）。"""
        try:
            from config.settings import get_models_config

            root = (get_models_config().get("local_model_root") or "").strip()
            if not root:
                return BgeM3Embedder.MODEL_NAME
            base = Path(root.replace("/", os.sep))
            for candidate in (base / "bge-m3", base / "BAAI" / "bge-m3"):
                if candidate.is_dir() and any(candidate.iterdir()):
                    return str(candidate)
        except Exception:
            pass
        return BgeM3Embedder.MODEL_NAME

    def __init__(self, device: str | None = None):
        from sentence_transformers import SentenceTransformer
        import torch

        model_ref = self._resolve_model_id_or_path()
        hub_id = self.MODEL_NAME

        def _load_model(target_device: str):
            p = Path(model_ref)
            # 仅当本地目录存在且含 config.json 时才强制离线；否则对 Hub 名误用 local_files_only
            # 会在缓存不完整时抛出 OSError/AttributeError，且第二次联网加载长时间无输出像「卡死」。
            if p.is_dir() and (p / "config.json").is_file():
                try:
                    return SentenceTransformer(
                        str(p),
                        device=target_device,
                        local_files_only=True,
                    )
                except Exception as e:
                    print(
                        f"[Embedder] Local dir load failed ({type(e).__name__}), "
                        f"retrying without local_files_only...",
                        flush=True,
                    )
                    return SentenceTransformer(str(p), device=target_device)
            print(
                "[Embedder] Loading from Hugging Face cache / mirror (first run may download several GB; wait)…",
                flush=True,
            )
            return SentenceTransformer(hub_id, device=target_device)

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        print(f"[Embedder] Loading {model_ref} on {device} (prefer local cache) ...")
        try:
            self._model = _load_model(device)
        except Exception as e:
            if device == "cuda":
                print(f"[Embedder] CUDA load failed ({type(e).__name__}), fallback to CPU...")
                self._model = _load_model("cpu")
                device = "cpu"
            else:
                raise
        self._dim = int(self._model.get_sentence_embedding_dimension() or 1024)
        # Stage 1a 吞吐保护：限制 BGE 编码最大 token 长度，避免长文触发注意力复杂度爆炸。
        try:
            mx = int(os.getenv("BGE_MAX_SEQ_LENGTH", "1024"))
        except ValueError:
            mx = 1024
        mx = max(128, min(mx, 8192))
        try:
            self._model.max_seq_length = mx
            print(f"[Embedder] max_seq_length={mx}", flush=True)
        except Exception:
            pass
        print(f"[Embedder] Ready. model={model_ref}, dim={self._dim}, device={device}")

    @property
    def dim(self) -> int:
        return self._dim

    def encode(
        self,
        texts: List[str],
        batch_size: int = 64,
        *,
        show_progress_bar: bool | None = None,
    ) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)

        if show_progress_bar is None:
            show_progress_bar = len(texts) > 200

        vecs = self._model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=show_progress_bar,
            convert_to_numpy=True,
        )
        return np.asarray(vecs, dtype=np.float32)

    def unload_model(self) -> None:
        """显式释放 sentence-transformers 模型引用，供两阶段流水线在 Phase 2 前腾出 VRAM。"""
        self._model = None
        self._dim = 0


class TeiRemoteEmbedder:
    """
    BGE-M3 via Hugging Face Text Embeddings Inference (TEI) HTTP API.

    Set env ``BGE_TEI_URL`` (e.g. ``http://127.0.0.1:8080``) to use; no local GPU required.
    TEI must serve ``BAAI/bge-m3`` (or a compatible 1024-dim checkpoint). Native endpoint: ``POST /embed``.
    """

    MODEL_NAME = "BAAI/bge-m3"

    def __init__(self, base_url: str) -> None:
        import httpx

        self._base = base_url.rstrip("/")
        read_s = float(os.getenv("BGE_TEI_READ_TIMEOUT", "600"))
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=30.0, read=read_s, write=120.0, pool=30.0)
        )
        self._dim = self._resolve_dim()
        print(
            f"[Embedder] TEI remote {self._base} dim={self._dim} "
            f"(BGE-M3 served in Docker; this process has no local model)",
            flush=True,
        )

    def _resolve_dim(self) -> int:
        raw = (os.getenv("BGE_TEI_DIM") or "").strip()
        if raw.isdigit():
            return max(1, int(raw))
        try:
            r = self._client.post(
                f"{self._base}/embed",
                json={"inputs": ["_probe_"]},
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            arr = _parse_tei_embed_json(r.json())
            if arr.shape[1] > 0:
                return int(arr.shape[1])
        except Exception as e:
            print(
                f"[Embedder] TEI dim probe failed ({type(e).__name__}: {e}); default dim=1024",
                flush=True,
            )
        return 1024

    @property
    def dim(self) -> int:
        return self._dim if self._dim > 0 else 1024

    def encode(
        self,
        texts: List[str],
        batch_size: int = 64,
        *,
        show_progress_bar: bool | None = None,
    ) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)

        try:
            tei_bs = int(os.getenv("BGE_TEI_BATCH_SIZE", str(max(1, batch_size))))
        except ValueError:
            tei_bs = max(1, batch_size)
        tei_bs = max(1, min(tei_bs, 512))

        l2 = (os.getenv("BGE_TEI_L2_NORMALIZE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        if show_progress_bar is None:
            show_progress_bar = len(texts) > 200

        chunks: List[tuple[int, List[str]]] = []
        for i in range(0, len(texts), tei_bs):
            chunks.append((i, texts[i : i + tei_bs]))

        iterator = chunks
        if show_progress_bar:
            try:
                from tqdm import tqdm

                iterator = tqdm(
                    chunks,
                    desc="TEI /embed",
                    unit="batch",
                )
            except ImportError:
                pass

        out: Optional[np.ndarray] = None
        for start, batch in iterator:
            r = self._client.post(
                f"{self._base}/embed",
                json={"inputs": batch},
                headers={"Content-Type": "application/json"},
            )
            if r.status_code >= 400:
                raise RuntimeError(
                    f"TEI /embed HTTP {r.status_code}: {r.text[:500]}"
                )
            part = _parse_tei_embed_json(r.json())
            if part.shape[0] != len(batch):
                raise RuntimeError(
                    f"TEI /embed rows mismatch: got {part.shape[0]} vectors for {len(batch)} inputs"
                )
            if l2:
                part = _l2_normalize_rows(part)
            if out is None:
                out = np.zeros((len(texts), part.shape[1]), dtype=np.float32)
            out[start : start + len(batch), :] = part

        assert out is not None
        return out

    def unload_model(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        self._dim = 0


_EMBEDDER_SINGLETON: Optional[Union[BgeM3Embedder, TeiRemoteEmbedder]] = None


def _tei_base_url() -> str:
    return (os.getenv("BGE_TEI_URL") or os.getenv("TEI_EMBEDDING_BASE_URL") or "").strip()


def get_embedder() -> EmbedderProtocol:
    """Singleton BGE-M3: local sentence-transformers, or TEI if ``BGE_TEI_URL`` is set."""
    global _EMBEDDER_SINGLETON
    if _EMBEDDER_SINGLETON is None:
        tei = _tei_base_url()
        if tei:
            _EMBEDDER_SINGLETON = TeiRemoteEmbedder(tei)
        else:
            _EMBEDDER_SINGLETON = BgeM3Embedder()
    return _EMBEDDER_SINGLETON


def unload_embedder() -> None:
    """销毁全局单例并尽力释放 GPU 显存（与 GLiNER 互斥加载前必须调用）。"""
    if os.environ.get("KEEP_EMBEDDER_LOADED") == "1":
        # 历史模拟器等长循环：块与块之间保留 BGE，避免反复加载；由调度脚本结束时清标志并再调本函数
        return
    global _EMBEDDER_SINGLETON
    inst = _EMBEDDER_SINGLETON
    _EMBEDDER_SINGLETON = None
    if inst is not None:
        try:
            inst.unload_model()
        except Exception:
            pass
        del inst
    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
    except Exception:
        pass
