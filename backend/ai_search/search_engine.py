from __future__ import annotations

import asyncio
import math
import re
import sys
from typing import Any, Iterable, Optional

import requests
from pydantic import BaseModel, Field, HttpUrl, ValidationError

try:
    from crawl4ai import AsyncWebCrawler
except Exception as e:  # pragma: no cover
    raise ImportError(
        "缺少依赖 crawl4ai。请先安装：python -m pip install crawl4ai"
    ) from e


class SearchResult(BaseModel):
    title: str = Field(min_length=1)
    url: HttpUrl
    content: str = Field(default="")


class LocalSearchEngine:
    """
    对接本地 SearXNG + Crawl4AI 的轻量搜索引擎封装。
    - SearXNG: http://localhost:8080/search?format=json&q=...
    - Crawl4AI: AsyncWebCrawler 并发抓取并转 Markdown
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8081",
        timeout_s: float = 10.0,
        user_agent: str = "datasearch-local-agent/1.0",
        max_concurrency: int = 5,
        embedding_base_url: str = "http://localhost:8080",
        embedding_model: str = "bge-m3",
    ) -> None:
        # Windows 控制台常见编码为 GBK，Crawl4AI/rich 日志里含 ✓/→ 等符号时可能触发 UnicodeEncodeError
        # 这里把 stdout/stderr 统一切到 UTF-8 并用 replace，避免抓取流程因日志输出崩溃
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.user_agent = user_agent
        self.max_concurrency = max(1, int(max_concurrency))
        self.embedding_base_url = embedding_base_url.rstrip("/")
        self.embedding_model = embedding_model

    async def search(self, query: str, limit: int = 3) -> list[SearchResult]:
        """
        访问本地 SearXNG，提取 title/url/content（摘要），只返回前 limit 条。
        """
        if not isinstance(query, str) or not query.strip():
            return []
        limit = max(1, int(limit))

        def _do_request() -> dict[str, Any]:
            url = f"{self.base_url}/search"
            resp = requests.get(
                url,
                params={"q": query, "format": "json"},
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            return resp.json()

        try:
            data = await asyncio.to_thread(_do_request)
        except requests.RequestException:
            # SearXNG 未启动/端口被占用/路径不对：按“无结果”处理，避免中断主流程
            return []
        raw_results = data.get("results") or []
        if not isinstance(raw_results, list):
            return []

        parsed: list[SearchResult] = []
        for item in raw_results[:limit]:
            if not isinstance(item, dict):
                continue
            payload = {
                "title": (item.get("title") or "").strip(),
                "url": item.get("url") or item.get("link") or "",
                "content": (item.get("content") or item.get("snippet") or "").strip(),
            }
            try:
                parsed.append(self._validate_search_result(payload))
            except ValidationError:
                continue
        return parsed

    async def crawl_and_format(self, urls: list, query: str) -> str:
        """
        并发抓取 URL，利用 Crawl4AI 的 Markdown 转换；清洗后做“语义切片与过滤”，
        汇总成编号文本块。单页失败跳过，不中断整体流程。
        """
        if not isinstance(urls, list) or not urls:
            return ""
        if not isinstance(query, str) or not query.strip():
            return ""

        query = query.strip()

        # 去重且过滤明显非法值，保持输入顺序
        seen: set[str] = set()
        deduped: list[str] = []
        for u in urls:
            if not isinstance(u, str):
                continue
            u = u.strip()
            if not u or u in seen:
                continue
            seen.add(u)
            deduped.append(u)

        if not deduped:
            return ""

        # 预先向量化 query（失败则整体退化为“按开头段落拼接”，但仍不做硬截断）
        query_vec = await self._get_embedding(query)

        sem = asyncio.Semaphore(self.max_concurrency)

        async def _crawl_one(target_url: str) -> Optional[dict[str, str]]:
            async with sem:
                try:
                    async with AsyncWebCrawler() as crawler:
                        result = await crawler.arun(url=target_url)
                except Exception:
                    return None

            md = self._extract_markdown(result)
            if not md:
                return None

            title = self._extract_title(result) or ""
            # 清洗仅去噪，不做字符硬截断；截断逻辑改为语义筛选后的“总长约 1000”
            cleaned = self._clean_markdown(md, limit=None)
            if not cleaned.strip():
                return None

            content = await self._semantic_slice(
                cleaned_markdown=cleaned,
                query_vec=query_vec,
                max_paragraphs=4,
                target_total_chars=1000,
            )
            if not content.strip():
                return None
            return {"title": title.strip(), "url": target_url, "content": content.strip()}

        tasks = [asyncio.create_task(_crawl_one(u)) for u in deduped]
        pages = await asyncio.gather(*tasks, return_exceptions=False)
        ok_pages = [p for p in pages if isinstance(p, dict)]

        if not ok_pages:
            return ""

        blocks: list[str] = []
        for idx, p in enumerate(ok_pages, start=1):
            blocks.append(
                f"[{idx}] 标题: {p.get('title','')} | URL: {p.get('url','')}\n"
                f"内容: {p.get('content','')}\n"
                f"---"
            )
        return "\n".join(blocks).strip()

    def _extract_markdown(self, crawl_result: Any) -> str:
        """
        尽量从 Crawl4AI 返回对象中提取 Markdown 内容。
        不同版本字段可能不同，这里做兼容性提取。
        """
        # 常见：result.markdown / result.markdown_v2 / result.markdown.raw_markdown
        for path in (
            ("markdown",),
            ("markdown_v2",),
            ("markdown", "raw_markdown"),
            ("markdown_v2", "raw_markdown"),
            ("markdown", "markdown"),
            ("markdown_v2", "markdown"),
            ("content",),
            ("text",),
        ):
            v = self._get_attr_path(crawl_result, path)
            if isinstance(v, str) and v.strip():
                return v
        return ""

    def _extract_title(self, crawl_result: Any) -> str:
        for path in (
            ("metadata", "title"),
            ("title",),
            ("page_title",),
        ):
            v = self._get_attr_path(crawl_result, path)
            if isinstance(v, str) and v.strip():
                return v
        return ""

    @staticmethod
    def _get_attr_path(obj: Any, path: Iterable[str]) -> Any:
        cur = obj
        for key in path:
            if cur is None:
                return None
            if isinstance(cur, dict):
                cur = cur.get(key)
            else:
                cur = getattr(cur, key, None)
        return cur

    @staticmethod
    def _clean_markdown(md: str, limit: int | None = 1500) -> str:
        """
        针对小上下文模型的简单清洗：
        - 去掉图片 Markdown: ![alt](url)
        - 去掉过长的 HTML 标签残留（如 <div ...> / </div> / <span ...>）
        - 压缩空行
        - 可选：强制截断（limit=None 时禁用）
        """
        if not md:
            return ""

        # 1) 去图片
        md = re.sub(r"!\[[^\]]*]\([^)]+\)", "", md)

        # 2) 去 HTML 标签（包含属性），并处理极长的“残留片段”
        md = re.sub(r"</?[^>\n]{1,200}?>", "", md)
        md = re.sub(r"<[^>\n]{200,}>", "", md)

        # 3) 清理多余空白
        md = md.replace("\r\n", "\n").replace("\r", "\n")
        md = re.sub(r"\n{3,}", "\n\n", md).strip()

        # 4) 可选截断（旧逻辑兼容）
        if limit is not None:
            hard_max = max(1000, int(limit))
            if len(md) > hard_max:
                md = md[:hard_max].rstrip()
        return md

    async def _semantic_slice(
        self,
        *,
        cleaned_markdown: str,
        query_vec: list[float] | None,
        max_paragraphs: int = 4,
        target_total_chars: int = 1000,
    ) -> str:
        """
        将 Markdown 按段落切分，做 embedding 相似度排序，取 Top 段落重组到约 target_total_chars。
        """
        text = (cleaned_markdown or "").strip()
        if not text:
            return ""

        # 优先按“空行分段”（兼容 \n\n 与 \n\s*\n）
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text)]
        paragraphs = [p for p in paragraphs if len(p) >= 20]
        # 兜底：有些页面 Markdown 几乎没有空行，退化为按行聚合
        if not paragraphs:
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            buf: list[str] = []
            cur = ""
            for ln in lines:
                # 避免把超长一行直接塞爆；分块聚合成“段落”
                if len(cur) + len(ln) + 1 > 400:
                    if len(cur) >= 20:
                        buf.append(cur)
                    cur = ln
                else:
                    cur = (cur + " " + ln).strip() if cur else ln
            if len(cur) >= 20:
                buf.append(cur)
            paragraphs = buf
        if not paragraphs:
            return ""

        # 若 query embedding 不可用，退化为取前若干段（仍限制总长 ~1000）
        if not query_vec:
            return self._join_with_budget(paragraphs[:max_paragraphs], target_total_chars)

        # 批量向量化段落（失败则退化）
        para_vecs = await self._get_embeddings(paragraphs)
        if not para_vecs or len(para_vecs) != len(paragraphs):
            return self._join_with_budget(paragraphs[:max_paragraphs], target_total_chars)

        scored: list[tuple[float, str]] = []
        for p, v in zip(paragraphs, para_vecs):
            if not v:
                continue
            scored.append((self._cosine_similarity(query_vec, v), p))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [p for _, p in scored[: max(1, int(max_paragraphs))]]
        return self._join_with_budget(top, target_total_chars)

    @staticmethod
    def _join_with_budget(parts: list[str], budget: int) -> str:
        """
        用 ' ... ' 拼接，并控制总长度在 budget 左右。
        """
        budget = max(100, int(budget))
        out: list[str] = []
        total = 0
        sep = " ... "
        for p in parts:
            p = (p or "").strip()
            if not p:
                continue
            add_len = len(p) + (len(sep) if out else 0)
            if total + add_len > budget:
                remain = budget - total - (len(sep) if out else 0)
                if remain > 80:
                    out.append((sep if out else "") + p[:remain].rstrip())
                break
            out.append((sep if out else "") + p)
            total += add_len
        return "".join(out).strip()

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return -1.0
        n = min(len(a), len(b))
        if n == 0:
            return -1.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for i in range(n):
            x = float(a[i])
            y = float(b[i])
            dot += x * y
            na += x * x
            nb += y * y
        if na <= 0.0 or nb <= 0.0:
            return -1.0
        return dot / (math.sqrt(na) * math.sqrt(nb))

    async def _get_embedding(self, text: str) -> list[float] | None:
        """
        获取单条 embedding（默认走 OpenAI embeddings 兼容格式）。
        """
        vecs = await self._get_embeddings([text])
        if vecs and vecs[0]:
            return vecs[0]
        return None

    async def _get_embeddings(self, texts: list[str]) -> list[list[float]] | None:
        """
        批量获取 embeddings。
        默认尝试 OpenAI 兼容：POST {base}/v1/embeddings，body={"model":..., "input":[...]}
        若 404，再尝试 {base}/embeddings。
        """
        inputs = [t for t in (texts or []) if isinstance(t, str) and t.strip()]
        if not inputs:
            return None

        candidates = [
            f"{self.embedding_base_url}/v1/embeddings",
            f"{self.embedding_base_url}/embeddings",
        ]

        payload = {"model": self.embedding_model, "input": inputs}

        def _do_post(url: str) -> tuple[int, Any, str]:
            try:
                r = requests.post(url, json=payload, timeout=max(10.0, self.timeout_s))
                status = r.status_code
                try:
                    j = r.json() if r.content else {}
                except Exception:
                    j = {}
                return status, j, (r.text or "")[:500]
            except Exception as e:
                return 0, {}, str(e)

        last_text = ""
        for url in candidates:
            status, j, t = await asyncio.to_thread(_do_post, url)
            last_text = t
            if status == 404 or status == 0:
                continue
            if status != 200:
                # 非 200 直接放弃（避免反复打服务）
                return None

            # OpenAI: {"data":[{"embedding":[...]}...]}
            data = j.get("data") if isinstance(j, dict) else None
            if isinstance(data, list) and data:
                vecs: list[list[float]] = []
                for item in data:
                    emb = item.get("embedding") if isinstance(item, dict) else None
                    if isinstance(emb, list):
                        vecs.append([float(x) for x in emb])
                if len(vecs) == len(inputs):
                    return vecs
                # 有些实现只返回一条
                if len(vecs) == 1 and len(inputs) == 1:
                    return vecs

            # 兼容一些实现：{"embeddings":[[...],[...]]}
            embs = j.get("embeddings") if isinstance(j, dict) else None
            if isinstance(embs, list) and embs and all(isinstance(x, list) for x in embs):
                return [[float(v) for v in row] for row in embs]  # type: ignore[return-value]

            return None

        return None

    @staticmethod
    def _validate_search_result(payload: dict[str, Any]) -> SearchResult:
        """
        兼容 Pydantic v1/v2 的校验入口。
        """
        if hasattr(SearchResult, "model_validate"):  # pydantic v2
            return SearchResult.model_validate(payload)  # type: ignore[attr-defined]
        return SearchResult.parse_obj(payload)  # pydantic v1


__all__ = ["LocalSearchEngine", "SearchResult"]

