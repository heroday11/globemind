import React from 'react';
import {
  AbsoluteFill,
  Easing,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

type Segment = {
  start: number;
  end: number;
  title: string;
  kicker: string;
  body: string;
  accent: string;
  cardSide?: 'left' | 'right';
  highlight?: {
    x: number;
    y: number;
    width: number;
    height: number;
    label?: string;
  };
};

const sourceOffsetSeconds = 5;
const sourceOffsetFrames = sourceOffsetSeconds * 30;
const fadeOutSeconds = 0.18;

const segments: Segment[] = [
  {
    start: 5,
    end: 7.32,
    kicker: 'Platform Overview',
    title: 'GlobeMind 平台总览',
    body: '从首页进入全球新闻、舆情、数据服务与智库能力，形成一体化情报工作台。',
    accent: '#38bdf8',
    highlight: {x: 430, y: 140, width: 1080, height: 720, label: '核心能力入口'},
  },
  {
    start: 7.42,
    end: 22.82,
    kicker: 'Sentiment Intelligence',
    title: '智能舆情分析',
    body: '高危目标指数、情报短报、敏感议题和趋势曲线，帮助快速定位风险变化。',
    accent: '#2563eb',
    highlight: {x: 20, y: 205, width: 1585, height: 770, label: '指数、短报与趋势曲线'},
  },
  {
    start: 23.72,
    end: 30.78,
    kicker: 'Evidence Drilldown',
    title: '新闻证据页',
    body: '从舆情结果下钻到原文证据，同屏查看来源信息、正文、事件抽取和翻译栏目。',
    accent: '#14b8a6',
    highlight: {x: 20, y: 145, width: 1535, height: 865, label: '来源信息、正文、事件抽取与翻译'},
  },
  {
    start: 31.05,
    end: 43.82,
    kicker: 'Risk Attribution',
    title: '异常定位与影响新闻',
    body: '在曲线上定位异常时间点，再展开右侧影响新闻，解释指数波动来自哪些报道。',
    accent: '#f43f5e',
    highlight: {x: 90, y: 565, width: 1510, height: 410, label: '趋势异常与证据列表'},
  },
  {
    start: 51.95,
    end: 59.22,
    kicker: 'Story Graph',
    title: '大事件脉络图谱',
    body: '把宏观事件拆成 L3 大事件、L2 支线和影响关系，用节点图观察演化路径。',
    accent: '#0f766e',
    highlight: {x: 250, y: 255, width: 1380, height: 600, label: '事件节点与关系边'},
  },
  {
    start: 59.65,
    end: 72.82,
    kicker: 'Data Search',
    title: '新闻事件检索台',
    body: '支持关键词、时间、语种、来源和 L1/L2/L3 层级检索，并沉淀到工作文件夹。',
    accent: '#0284c7',
    highlight: {x: 255, y: 190, width: 1640, height: 340, label: '检索条件与数据范围'},
  },
  {
    start: 77.45,
    end: 79.88,
    kicker: 'Event Retrieval',
    title: 'L1 事件检索',
    body: '以“芯片”为例切换到 L1 事件，检索结构化事件和相关新闻证据。',
    accent: '#7c3aed',
    highlight: {x: 270, y: 205, width: 1310, height: 330, label: '关键词、层级与结果列表'},
  },
  {
    start: 81.02,
    end: 86.62,
    kicker: 'Analysis Drawer',
    title: '左侧侧滑分析面板',
    body: '展开新闻详情的左侧分析面板，集中查看涉华判定、情绪、事件标签和辅助研判信息。',
    accent: '#0ea5e9',
    cardSide: 'right',
    highlight: {x: 10, y: 145, width: 455, height: 865, label: '左侧侧滑分析面板'},
  },
  {
    start: 92.95,
    end: 95.65,
    kicker: 'Data Assistant',
    title: 'AI 自主分析历史会话',
    body: '浏览 ID 67「最近全球发生了什么大事」，展示 AI 如何拆解问题、调用接口并结构化回复。',
    accent: '#2563eb',
    highlight: {x: 84, y: 245, width: 1240, height: 740, label: 'ID 67 会话与结构化回复'},
  },
  {
    start: 96.1,
    end: 106.62,
    kicker: 'Knowledge Context',
    title: '知识库上下文',
    body: 'Data Assistant 可挂载专家 Skill、数据库连接和知识库文件，让后续分析有可审查上下文。',
    accent: '#16a34a',
    highlight: {x: 330, y: 180, width: 1555, height: 780, label: 'Skill、文件与知识库'},
  },
  {
    start: 107.92,
    end: 115.82,
    kicker: 'World State Terminal',
    title: '世界状态终端',
    body: '把经济、能源、供应链、科技和社会风险指标组织为可监控的综合状态面板。',
    accent: '#0891b2',
    highlight: {x: 15, y: 300, width: 1625, height: 710, label: '指标、信号与趋势'},
  },
  {
    start: 116.35,
    end: 127.78,
    kicker: 'Expert Skill Market',
    title: '专家 Skill 与数据库装配',
    body: '在 Academic Data 中选择领域专家 Skill，并配置可信数据库作为 Agent 的能力底座。',
    accent: '#0d9488',
    highlight: {x: 305, y: 145, width: 1230, height: 850, label: '专家 Skill 市场'},
  },
  {
    start: 137.36,
    end: 141.25,
    kicker: 'API Routing',
    title: 'API Routing 配置演示',
    body: '展示如何填写 API Key、选择模型与路由参数，让平台接入外部模型服务。',
    accent: '#7c3aed',
    highlight: {x: 900, y: 155, width: 560, height: 855, label: 'API Key、模型与请求示例'},
  },
];

const formatTime = (seconds: number) => {
  const safe = Math.max(0, Math.floor(seconds));
  const min = Math.floor(safe / 60)
    .toString()
    .padStart(2, '0');
  const sec = (safe % 60).toString().padStart(2, '0');
  return `${min}:${sec}`;
};

const currentSegment = (sourceSeconds: number): Segment | null => {
  return segments.find((segment) => sourceSeconds >= segment.start && sourceSeconds < segment.end) ?? null;
};

const PrivacyBars = () => {
  return (
    <>
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: '100%',
          height: 86,
          background:
            'linear-gradient(90deg, rgba(248,250,252,0.98), rgba(239,246,255,0.98) 55%, rgba(248,250,252,0.98))',
          boxShadow: '0 10px 30px rgba(15, 23, 42, 0.08)',
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: 0,
          bottom: 0,
          width: '100%',
          height: 42,
          background: 'rgba(248,250,252,0.98)',
          boxShadow: '0 -8px 22px rgba(15, 23, 42, 0.06)',
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: 34,
          top: 19,
          display: 'flex',
          alignItems: 'center',
          gap: 13,
          color: '#0f172a',
          fontFamily:
            '"Inter", "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", system-ui, sans-serif',
        }}
      >
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 8,
            background: 'linear-gradient(135deg, #1d4ed8, #22d3ee)',
            display: 'grid',
            placeItems: 'center',
            color: 'white',
            fontWeight: 900,
            fontSize: 20,
            boxShadow: '0 14px 28px rgba(37, 99, 235, 0.28)',
          }}
        >
          G
        </div>
        <div>
          <div style={{fontSize: 22, fontWeight: 850, lineHeight: 1.05}}>GlobeMind</div>
          <div style={{fontSize: 12, color: '#64748b', marginTop: 5, letterSpacing: 1.4}}>
            GLOBAL INTELLIGENCE WORKFLOW DEMO
          </div>
        </div>
      </div>
      <div
        style={{
          position: 'absolute',
          right: 34,
          top: 25,
          padding: '10px 14px',
          borderRadius: 8,
          border: '1px solid rgba(15, 23, 42, 0.09)',
          background: 'rgba(255,255,255,0.86)',
          color: '#334155',
          fontSize: 14,
          fontFamily:
            '"Inter", "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", system-ui, sans-serif',
        }}
      >
        录屏界面已做浏览器栏与系统栏遮罩
      </div>
    </>
  );
};

const ProgressLine = ({progress}: {progress: number}) => {
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        bottom: 42,
        width: '100%',
        height: 5,
        background: 'rgba(15, 23, 42, 0.06)',
      }}
    >
      <div
        style={{
          width: `${Math.max(0, Math.min(1, progress)) * 100}%`,
          height: '100%',
          background: 'linear-gradient(90deg, #0ea5e9, #22c55e, #a78bfa)',
          boxShadow: '0 -1px 18px rgba(14, 165, 233, 0.45)',
        }}
      />
    </div>
  );
};

const SegmentCard = ({segment}: {segment: Segment}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sourceSeconds = frame / fps + sourceOffsetSeconds;
  const localFrame = Math.max(0, (sourceSeconds - segment.start) * fps);
  const enter = interpolate(localFrame, [0, fps * 0.22], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });
  const exit = interpolate(sourceSeconds, [segment.end - fadeOutSeconds, segment.end], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.ease,
  });
  const opacity = Math.min(enter, exit);
  const translateY = interpolate(enter, [0, 1], [28, 0]);

  return (
    <div
      style={{
        position: 'absolute',
        left: segment.cardSide === 'right' ? undefined : 44,
        right: segment.cardSide === 'right' ? 44 : undefined,
        bottom: 74,
        width: 670,
        opacity,
        transform: `translateY(${translateY}px)`,
        color: '#f8fafc',
        fontFamily:
          '"Inter", "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", system-ui, sans-serif',
      }}
    >
      <div
        style={{
          borderRadius: 8,
          overflow: 'hidden',
          background: 'rgba(15, 23, 42, 0.84)',
          border: '1px solid rgba(255, 255, 255, 0.16)',
          boxShadow: '0 24px 70px rgba(2, 8, 23, 0.32)',
        }}
      >
        <div
          style={{
            height: 5,
            background: `linear-gradient(90deg, ${segment.accent}, rgba(255,255,255,0.2))`,
          }}
        />
        <div style={{padding: '22px 26px 24px'}}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              marginBottom: 10,
              color: '#bae6fd',
              fontSize: 13,
              fontWeight: 850,
              letterSpacing: 1.2,
              textTransform: 'uppercase',
            }}
          >
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: 99,
                background: segment.accent,
                boxShadow: `0 0 18px ${segment.accent}`,
              }}
            />
            {segment.kicker}
          </div>
          <div style={{fontSize: 34, lineHeight: 1.15, fontWeight: 900, marginBottom: 12}}>
            {segment.title}
          </div>
          <div style={{fontSize: 18, lineHeight: 1.55, color: 'rgba(248, 250, 252, 0.88)'}}>
            {segment.body}
          </div>
        </div>
      </div>
    </div>
  );
};

const Highlight = ({segment}: {segment: Segment}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sourceSeconds = frame / fps + sourceOffsetSeconds;
  if (!segment.highlight) return null;

  const localFrame = Math.max(0, (sourceSeconds - segment.start) * fps);
  const enter = interpolate(localFrame, [0, fps * 0.18], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });
  const exit = interpolate(sourceSeconds, [segment.end - fadeOutSeconds, segment.end], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const pulse = 0.55 + Math.sin(frame / 10) * 0.18;
  const opacity = Math.min(enter, exit);
  const {x, y, width, height, label} = segment.highlight;

  return (
    <div
      style={{
        position: 'absolute',
        left: x,
        top: y,
        width,
        height,
        opacity,
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          position: 'absolute',
          inset: 0,
          borderRadius: 8,
          border: `3px solid ${segment.accent}`,
          boxShadow: `0 0 ${24 + pulse * 18}px ${segment.accent}55, inset 0 0 0 1px rgba(255,255,255,0.72)`,
          background: `${segment.accent}12`,
        }}
      />
      {label ? (
        <div
          style={{
            position: 'absolute',
            right: 12,
            top: -42,
            borderRadius: 8,
            padding: '9px 13px',
            background: 'rgba(15, 23, 42, 0.88)',
            color: '#f8fafc',
            fontSize: 15,
            fontWeight: 800,
            fontFamily:
              '"Inter", "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", system-ui, sans-serif',
            boxShadow: '0 12px 28px rgba(15, 23, 42, 0.22)',
            whiteSpace: 'nowrap',
          }}
        >
          {label}
        </div>
      ) : null}
    </div>
  );
};

const ChapterPill = ({segment}: {segment: Segment}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sourceSeconds = frame / fps + sourceOffsetSeconds;
  const displaySeconds = sourceSeconds - sourceOffsetSeconds;

  return (
    <div
      style={{
        position: 'absolute',
        top: 22,
        left: '50%',
        transform: 'translateX(-50%)',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        height: 44,
        padding: '0 18px',
        borderRadius: 8,
        background: 'rgba(255,255,255,0.86)',
        border: '1px solid rgba(15, 23, 42, 0.08)',
        boxShadow: '0 16px 38px rgba(15, 23, 42, 0.08)',
        fontFamily:
          '"Inter", "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", system-ui, sans-serif',
      }}
    >
      <span style={{fontSize: 13, color: '#64748b', fontWeight: 750}}>
        {formatTime(displaySeconds)}
      </span>
      <span style={{width: 1, height: 18, background: 'rgba(100, 116, 139, 0.25)'}} />
      <span style={{fontSize: 16, color: '#0f172a', fontWeight: 850}}>{segment.title}</span>
    </div>
  );
};

export const GlobemindDemo = () => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const sourceSeconds = frame / fps + sourceOffsetSeconds;
  const segment = currentSegment(sourceSeconds);
  const progress = frame / Math.max(1, durationInFrames - 1);

  return (
    <AbsoluteFill style={{backgroundColor: '#f8fafc'}}>
      <OffthreadVideo
        src={staticFile('source.mp4')}
        startFrom={sourceOffsetFrames}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
        }}
      />
      <PrivacyBars />
      {segment ? <Highlight segment={segment} /> : null}
      {segment ? <ChapterPill segment={segment} /> : null}
      {segment ? <SegmentCard segment={segment} /> : null}
      <ProgressLine progress={progress} />
      <Sequence from={0} durationInFrames={54}>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background: 'linear-gradient(135deg, rgba(15,23,42,0.72), rgba(14,165,233,0.30))',
            display: 'grid',
            placeItems: 'center',
            color: 'white',
            fontFamily:
              '"Inter", "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", system-ui, sans-serif',
          }}
        >
          <div style={{textAlign: 'center'}}>
            <div style={{fontSize: 66, fontWeight: 950, marginBottom: 18}}>
              GlobeMind 产品演示
            </div>
            <div style={{fontSize: 26, lineHeight: 1.45, color: 'rgba(255,255,255,0.86)'}}>
              全球新闻监测 · 舆情研判 · 事件图谱 · AI 数据助手
            </div>
          </div>
        </div>
      </Sequence>
    </AbsoluteFill>
  );
};
