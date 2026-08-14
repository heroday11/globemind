#!/usr/bin/env python
from __future__ import annotations

"""
opinion_analyzer.py

地缘舆情研判引擎核心模块 — AdvancedOpinionAnalyzer

职责：
  纯计算逻辑层，接收标准化字典输入，输出高度结构化的 ClusterOpinionReport，
  为后续 Qwen2.5/Sailor2 大模型 Agent 提供弹药。

依赖：pydantic >= 2.7, numpy, python 标准库 math / datetime
"""

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Data Models
# ─────────────────────────────────────────────────────────────────────────────

class ArticleMeta(BaseModel):
    """单篇文章的标准化元数据模型。"""

    id: str = Field(..., description="文章唯一标识符")
    title: str = Field(..., description="文章标题")
    source_domain: str = Field(..., description="来源媒体域名，如 reuters.com")
    pub_time: datetime = Field(..., description="发布时间（含时区信息优先）")
    sentiment_score: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="情感分值，范围 [-1.0, 1.0]，负值为负面",
    )

    @field_validator("pub_time", mode="before")
    @classmethod
    def _parse_pub_time(cls, v: object) -> datetime:
        """允许字符串、时间戳整数或 datetime 对象作为输入。"""
        if isinstance(v, datetime):
            return v
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        if isinstance(v, str):
            # 兼容 ISO 8601 及常见格式（含微秒）
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    return datetime.strptime(v, fmt)
                except ValueError:
                    continue
            raise ValueError(f"无法解析时间字符串: {v!r}")
        raise TypeError(f"不支持的 pub_time 类型: {type(v)}")

    @field_validator("source_domain", mode="before")
    @classmethod
    def _normalise_domain(cls, v: str) -> str:
        """统一转小写并去除空格，方便媒体级别查表。"""
        return str(v).strip().lower()


class TimelineNode(BaseModel):
    """时间轴上的单个事件节点。"""

    timestamp: datetime = Field(..., description="该时间切片的起始时间")
    event_stage: str = Field(
        ...,
        description="事件阶段标签，如 首次潜伏 / 爆发引爆 / 持续发酵 / 平息衰退",
    )
    node_summary: str = Field(..., description="该切片内容摘要（Top 标题列表）")
    volume: float = Field(..., ge=0.0, description="该切片加权声量")


class ClusterOpinionReport(BaseModel):
    """事件簇舆情综合研判报告。"""

    cluster_id: int = Field(..., description="HDBSCAN 聚类 ID")
    is_china_related: bool = Field(..., description="是否涉华事件")
    weighted_volume: float = Field(..., ge=0.0, description="媒体影响力加权总声量")
    comprehensive_index: float = Field(..., description="舆情综合指数")
    timeline: List[TimelineNode] = Field(
        default_factory=list, description="事件演化时间轴节点列表"
    )
    top_media_distribution: Dict[str, float] = Field(
        default_factory=dict,
        description="高权重媒体声量分布，key=域名，value=加权声量占比",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="报告生成时间 (UTC)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# AdvancedOpinionAnalyzer
# ─────────────────────────────────────────────────────────────────────────────

CHINA_RELATED_KEYWORDS: frozenset[str] = frozenset(
    {
        "china", "chinese", "beijing", "prc", "ccp", "pla", "xinhua",
        "taiwan", "hongkong", "hong kong", "uyghur", "xinjiang", "tibet",
        "huawei", "tiktok", "bri", "belt and road", "南海", "台湾", "中国",
        "北京", "新疆", "香港", "华为", "解放军",
    }
)


class AdvancedOpinionAnalyzer:
    """
    高阶地缘舆情研判引擎。

    完全无状态（除媒体权重字典外），可安全并发调用。
    """

    # ── 媒体权重分级字典 ──────────────────────────────────────────────────────
    TIER_1_MEDIA: Dict[str, float] = {
        # 国际顶级线缆/报纸
        "reuters.com": 10.0,
        "apnews.com": 10.0,
        "nytimes.com": 10.0,
        "wsj.com": 10.0,
        "ft.com": 10.0,
        "economist.com": 10.0,
        "bbc.com": 10.0,
        "bbc.co.uk": 10.0,
        "theguardian.com": 10.0,
        "washingtonpost.com": 10.0,
        # 亚太关键节点
        "scmp.com": 10.0,
        "straitstimes.com": 10.0,
        "nikkei.com": 10.0,
        "channelnewsasia.com": 10.0,
    }

    TIER_2_MEDIA: Dict[str, float] = {
        # 二线权威媒体
        "cnn.com": 5.0,
        "nbcnews.com": 5.0,
        "abcnews.go.com": 5.0,
        "cbsnews.com": 5.0,
        "foxnews.com": 5.0,
        "aljazeera.com": 5.0,
        "dw.com": 5.0,
        "france24.com": 5.0,
        "euronews.com": 5.0,
        "bloomberg.com": 5.0,
        "time.com": 5.0,
        "newsweek.com": 5.0,
        "thehill.com": 5.0,
        "politico.com": 5.0,
        "axios.com": 5.0,
        "vox.com": 5.0,
        "theatlantic.com": 5.0,
        "foreignpolicy.com": 5.0,
        "foreignaffairs.com": 5.0,
        # 亚太二线
        "asahi.com": 5.0,
        "yomiuri.co.jp": 5.0,
        "hindustantimes.com": 5.0,
        "thehindu.com": 5.0,
        "bangkokpost.com": 5.0,
        "koreatimes.co.kr": 5.0,
    }

    # 未知媒体默认权重
    _DEFAULT_WEIGHT: float = 1.0

    def _get_media_weight(self, domain: str) -> float:
        """查询域名对应的媒体影响力权重。"""
        d = domain.lower().strip()
        if d in self.TIER_1_MEDIA:
            return self.TIER_1_MEDIA[d]
        if d in self.TIER_2_MEDIA:
            return self.TIER_2_MEDIA[d]
        return self._DEFAULT_WEIGHT

    # ── 1. 媒体霸权加权声量计算 ───────────────────────────────────────────────

    def _calculate_weighted_volume(
        self, articles: List[ArticleMeta]
    ) -> float:
        """
        计算整个文章集合的媒体影响力加权总声量。

        V_weighted = Σ weight(article.source_domain)  for each article
        """
        total: float = 0.0
        for art in articles:
            total += self._get_media_weight(art.source_domain)
        return total

    # ── 2. 舆情终极指数公式 ────────────────────────────────────────────────────

    def calculate_index(
        self,
        weighted_volume: float,
        avg_sentiment: float,
        hours_since_t0: float,
    ) -> float:
        """
        舆情综合指数公式：

            Index = log10(V_weighted + 1)
                    × (1 + 1.5 × |avg_sentiment|)
                    × e^(-0.05 × hours_since_t0)

        若 avg_sentiment < 0（负面抹黑），再乘以 1.2 破坏力惩罚系数。

        参数：
            weighted_volume  — 媒体加权总声量 (>=0)
            avg_sentiment    — 平均情感分值 [-1, 1]
            hours_since_t0   — 距事件发源点经过的小时数 (>=0)

        返回：
            float — 舆情综合指数（值越高，风险/影响力越大）
        """
        if weighted_volume < 0:
            weighted_volume = 0.0
        hours_since_t0 = max(0.0, hours_since_t0)

        log_volume: float = math.log10(weighted_volume + 1.0)
        sentiment_amplifier: float = 1.0 + 1.5 * abs(avg_sentiment)
        time_decay: float = math.exp(-0.05 * hours_since_t0)

        index: float = log_volume * sentiment_amplifier * time_decay

        # 负面抹黑额外惩罚系数
        if avg_sentiment < 0.0:
            index *= 1.2

        return round(index, 6)

    # ── 3. 事件演化时间轴提取 ──────────────────────────────────────────────────

    def extract_timeline(
        self,
        articles: List[ArticleMeta],
        bin_hours: int = 12,
    ) -> List[TimelineNode]:
        """
        将文章集合按 bin_hours 时间切片聚合，生成结构化时间轴。

        算法：
          1. 按 pub_time 升序排列
          2. 以首篇文章发布时间为 T0，按 bin_hours 分桶
          3. 统计每桶加权声量
          4. 声量最大桶 => T_peak（爆发引爆）
          5. T0 桶 => 首次潜伏；其余 => 持续发酵；尾部衰退桶 => 平息衰退

        返回：
            List[TimelineNode] — 按时间升序排列的节点列表
        """
        if not articles:
            return []

        sorted_arts = sorted(articles, key=lambda a: a.pub_time)

        # 统一为无时区感知 datetime 以便算术运算
        def _naive(dt: datetime) -> datetime:
            if dt.tzinfo is not None:
                return dt.replace(tzinfo=None)
            return dt

        t0_dt = _naive(sorted_arts[0].pub_time)

        # 分桶：key = 桶索引(int)
        bin_volume: Dict[int, float] = defaultdict(float)
        bin_titles: Dict[int, List[str]] = defaultdict(list)
        bin_times: Dict[int, datetime] = {}

        bin_seconds = bin_hours * 3600

        for art in sorted_arts:
            delta_sec = (_naive(art.pub_time) - t0_dt).total_seconds()
            bucket_idx = int(delta_sec // bin_seconds)
            w = self._get_media_weight(art.source_domain)
            bin_volume[bucket_idx] += w
            bin_titles[bucket_idx].append(art.title)
            if bucket_idx not in bin_times:
                bin_times[bucket_idx] = _naive(art.pub_time)

        if not bin_volume:
            return []

        # 找声量峰值桶
        peak_bucket = max(bin_volume, key=lambda k: bin_volume[k])

        nodes: List[TimelineNode] = []
        sorted_buckets = sorted(bin_volume.keys())
        last_bucket = sorted_buckets[-1]

        # 尾部衰退判定：若最后 2 个桶声量均低于峰值的 20%，视为平息区
        peak_vol = bin_volume[peak_bucket]
        decay_threshold = peak_vol * 0.20

        for idx in sorted_buckets:
            vol = bin_volume[idx]
            titles = bin_titles[idx]
            ts = bin_times[idx]

            # 事件阶段标签判定
            if idx == 0:
                stage = "首次潜伏"
            elif idx == peak_bucket:
                stage = "爆发引爆"
            elif idx == last_bucket and vol <= decay_threshold and idx > peak_bucket:
                stage = "平息衰退"
            else:
                stage = "持续发酵"

            # 节点摘要：最多展示 3 篇标题
            top_titles = titles[:3]
            summary = " | ".join(f"[{t[:60]}]" for t in top_titles)
            if len(titles) > 3:
                summary += f" …共 {len(titles)} 篇"

            nodes.append(
                TimelineNode(
                    timestamp=ts,
                    event_stage=stage,
                    node_summary=summary,
                    volume=round(vol, 4),
                )
            )

        return nodes

    # ── 4. 幕后推手推演 Prompt 构建器 ──────────────────────────────────────────

    def build_attribution_agent_prompt(
        self,
        report: ClusterOpinionReport,
    ) -> str:
        """
        构建用于 Qwen2.5/Sailor2 Agent 的结构化 XML 风格 Prompt。

        该 Prompt 整合：
          - 事件背景与综合指数
          - 时间轴节点（含阶段标签）
          - 高权重媒体分布
          - 负面情感加权信息
        引导模型进行利益归因、操纵迹象识别和幕后黑手推演。
        """
        # ── 时间轴 XML 片段
        timeline_xml_lines: list[str] = []
        for i, node in enumerate(report.timeline, start=1):
            ts_str = node.timestamp.strftime("%Y-%m-%d %H:%M")
            timeline_xml_lines.append(
                f'    <node index="{i}" timestamp="{ts_str}" '
                f'stage="{node.event_stage}" weighted_volume="{node.volume:.2f}">'
            )
            timeline_xml_lines.append(
                f'        <summary>{node.node_summary}</summary>'
            )
            timeline_xml_lines.append("    </node>")
        timeline_xml = "\n".join(timeline_xml_lines) if timeline_xml_lines else "    <node>（无时间轴数据）</node>"

        # ── 媒体分布 XML 片段
        media_xml_lines: list[str] = []
        sorted_media = sorted(
            report.top_media_distribution.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )
        for domain, share in sorted_media[:10]:   # 最多展示 Top-10
            tier = (
                "Tier-1" if domain in self.TIER_1_MEDIA
                else "Tier-2" if domain in self.TIER_2_MEDIA
                else "Unknown"
            )
            media_xml_lines.append(
                f'    <outlet domain="{domain}" tier="{tier}" volume_share="{share:.2%}"/>'
            )
        media_xml = "\n".join(media_xml_lines) if media_xml_lines else "    <outlet>（无媒体数据）</outlet>"

        # ── 情感极性描述
        neg_flag = "YES — 存在显著负面抹黑倾向" if report.comprehensive_index > 0 else "NO"
        # 从 timeline 反推平均情感（report 中未直接存储，用指数倒推标记）
        sentiment_note = (
            "综合指数已触发负面惩罚系数 ×1.2，研判存在定向负面叙事。"
            if report.comprehensive_index > 0
            else "情感倾向中性或正面。"
        )

        generated_str = report.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")

        prompt = f"""<AnalysisTask>

<Background>
    <cluster_id>{report.cluster_id}</cluster_id>
    <is_china_related>{"YES — 涉华事件" if report.is_china_related else "NO — 非涉华事件"}</is_china_related>
    <weighted_volume>{report.weighted_volume:.2f}</weighted_volume>
    <comprehensive_index>{report.comprehensive_index:.6f}</comprehensive_index>
    <negative_narrative_detected>{neg_flag}</negative_narrative_detected>
    <sentiment_note>{sentiment_note}</sentiment_note>
    <report_generated_at>{generated_str}</report_generated_at>
</Background>

<Timeline>
{timeline_xml}
</Timeline>

<Media_Distribution>
{media_xml}
</Media_Distribution>

<Task_Directives>
    <directive id="1" priority="CRITICAL">
        利益归因分析：基于上述媒体分布和时间轴，识别哪些国家、政治集团或
        商业利益方最可能是本次舆情的受益方（Cui Bono）。
        请以结构化列表输出，每个潜在受益方须附上置信度评分（0-100）和核心证据链。
    </directive>
    <directive id="2" priority="HIGH">
        操纵迹象扫描：分析时间轴中「爆发引爆」节点的媒体协同模式，
        判断是否存在以下任一迹象：
        (a) 多家 Tier-1 媒体在短时间窗口内同步报道同一叙事框架；
        (b) 声量曲线呈现非自然的阶跃式跳升（人工放大）；
        (c) 负面情感分值与声量峰值的时序高度吻合（定向炒作）。
        请对每条迹象给出 YES/NO/UNCERTAIN 判定及置信度。
    </directive>
    <directive id="3" priority="HIGH">
        幕后黑手推演（Hidden Hand Inference）：综合利益归因与操纵迹象，
        提出最多 3 个「幕后推手」假说，每个假说须包含：
        - 推手实体名称（国家/机构/个人）
        - 动机推断（Motive）
        - 操作路径假说（Operation Vector）
        - 反驳证据（Counter-Evidence，若有）
        - 综合可信度（Credibility: LOW / MEDIUM / HIGH / VERY HIGH）
    </directive>
    <directive id="4" priority="MEDIUM">
        后续监控建议：基于当前综合指数（{report.comprehensive_index:.4f}）
        和时间衰减趋势，预测未来 24-72 小时的舆情走向，
        并给出具体的监控关键词和预警阈值建议。
    </directive>
    <directive id="5" priority="LOW">
        反制叙事策略（Counter-Narrative Strategy）：
        若确认存在定向负面叙事，请提出 2-3 条基于事实的反制传播建议，
        明确目标受众、核心信息和推荐传播渠道。
    </directive>
</Task_Directives>

</AnalysisTask>"""
        return prompt

    # ── 5. 对外主接口 ─────────────────────────────────────────────────────────

    def generate_cluster_report(
        self,
        cluster_id: int,
        articles_data: List[dict],
    ) -> ClusterOpinionReport:
        """
        主入口：接收原始字典列表，返回完整的 ClusterOpinionReport。

        参数：
            cluster_id     — HDBSCAN 聚类 ID
            articles_data  — 原始文章字典列表，每条须包含 ArticleMeta 所需字段

        返回：
            ClusterOpinionReport — 结构化研判报告
        """
        if not articles_data:
            # 空簇：返回零值报告
            return ClusterOpinionReport(
                cluster_id=cluster_id,
                is_china_related=False,
                weighted_volume=0.0,
                comprehensive_index=0.0,
                timeline=[],
                top_media_distribution={},
            )

        # ── Step 1: 原始字典 → Pydantic 模型列表
        articles: List[ArticleMeta] = [
            ArticleMeta.model_validate(d) for d in articles_data
        ]

        # ── Step 2: 涉华检测（标题 + 域名关键词匹配）
        is_china_related = self._detect_china_related(articles)

        # ── Step 3: 加权声量
        weighted_volume = self._calculate_weighted_volume(articles)

        # ── Step 4: 平均情感分值
        avg_sentiment = self._calc_avg_sentiment(articles)

        # ── Step 5: 时间轴提取
        timeline = self.extract_timeline(articles)

        # ── Step 6: 计算距 T0 的小时数（用于时间衰减）
        hours_since_t0 = self._calc_hours_since_t0(articles)

        # ── Step 7: 综合指数
        comprehensive_index = self.calculate_index(
            weighted_volume=weighted_volume,
            avg_sentiment=avg_sentiment,
            hours_since_t0=hours_since_t0,
        )

        # ── Step 8: 高权重媒体分布（Tier-1 + Tier-2 占比）
        top_media_distribution = self._calc_media_distribution(articles)

        return ClusterOpinionReport(
            cluster_id=cluster_id,
            is_china_related=is_china_related,
            weighted_volume=round(weighted_volume, 4),
            comprehensive_index=comprehensive_index,
            timeline=timeline,
            top_media_distribution=top_media_distribution,
        )

    # ── 内部辅助方法 ───────────────────────────────────────────────────────────

    def _detect_china_related(self, articles: List[ArticleMeta]) -> bool:
        """检测文章集合是否与涉华话题相关（标题关键词 + 域名匹配）。"""
        for art in articles:
            text = (art.title + " " + art.source_domain).lower()
            for kw in CHINA_RELATED_KEYWORDS:
                if kw in text:
                    return True
        return False

    def _calc_avg_sentiment(
        self, articles: List[ArticleMeta]
    ) -> float:
        """计算加权平均情感分值（以媒体权重为权）。"""
        total_weight = 0.0
        weighted_sum = 0.0
        for art in articles:
            w = self._get_media_weight(art.source_domain)
            weighted_sum += w * art.sentiment_score
            total_weight += w
        if total_weight == 0.0:
            return 0.0
        return weighted_sum / total_weight

    def _calc_hours_since_t0(
        self, articles: List[ArticleMeta]
    ) -> float:
        """计算从最早文章到最新文章的时间跨度（小时）。"""
        if not articles:
            return 0.0

        def _naive(dt: datetime) -> datetime:
            return dt.replace(tzinfo=None) if dt.tzinfo else dt

        times = [_naive(a.pub_time) for a in articles]
        t_min = min(times)
        t_max = max(times)
        delta_hours = (t_max - t_min).total_seconds() / 3600.0
        return max(0.0, delta_hours)

    def _calc_media_distribution(
        self, articles: List[ArticleMeta]
    ) -> Dict[str, float]:
        """
        计算高权重媒体的声量分布占比。

        仅统计 Tier-1 和 Tier-2 媒体，返回各域名加权声量占总加权声量的比例。
        """
        domain_volume: Dict[str, float] = defaultdict(float)
        total_vol = 0.0

        for art in articles:
            d = art.source_domain
            if d in self.TIER_1_MEDIA or d in self.TIER_2_MEDIA:
                w = self._get_media_weight(d)
                domain_volume[d] += w
                total_vol += w

        if total_vol == 0.0:
            return {}

        return {
            domain: round(vol / total_vol, 6)
            for domain, vol in sorted(
                domain_volume.items(), key=lambda kv: kv[1], reverse=True
            )
        }


# ─────────────────────────────────────────────────────────────────────────────
# 快速自测入口（python -m agentic_rag.analysis.opinion_analyzer）
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":  # pragma: no cover
    _sample_articles = [
        {
            "id": "001",
            "title": "China military drills near Taiwan spark regional tension",
            "source_domain": "reuters.com",
            "pub_time": "2025-03-01T06:00:00",
            "sentiment_score": -0.72,
        },
        {
            "id": "002",
            "title": "Beijing denies provocation in South China Sea standoff",
            "source_domain": "bbc.com",
            "pub_time": "2025-03-01T09:30:00",
            "sentiment_score": -0.55,
        },
        {
            "id": "003",
            "title": "US Navy sends carrier group to Indo-Pacific amid China tensions",
            "source_domain": "wsj.com",
            "pub_time": "2025-03-01T14:15:00",
            "sentiment_score": -0.43,
        },
        {
            "id": "004",
            "title": "Regional economies brace for impact as geopolitical risk rises",
            "source_domain": "ft.com",
            "pub_time": "2025-03-02T03:00:00",
            "sentiment_score": -0.30,
        },
        {
            "id": "005",
            "title": "Local news site covers Taiwan strait developments",
            "source_domain": "localnews.example.com",
            "pub_time": "2025-03-02T07:45:00",
            "sentiment_score": -0.15,
        },
    ]

    analyzer = AdvancedOpinionAnalyzer()
    report = analyzer.generate_cluster_report(
        cluster_id=7,
        articles_data=_sample_articles,
    )

    import json

    print("\n====== ClusterOpinionReport ======")
    print(report.model_dump_json(indent=2))

    print("\n====== Attribution Agent Prompt ======")
    print(analyzer.build_attribution_agent_prompt(report))
 