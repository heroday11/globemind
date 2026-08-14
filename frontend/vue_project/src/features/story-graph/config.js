export const L2_RUN_ID = 'fast_l2_v1'
export const L3_RUN_ID = 'fast_l3_v1'

export const FIT_VIEW_OPTIONS = { padding: 0.12, duration: 0 }
export const INITIAL_FIT_VIEW_OPTIONS = { padding: 0.04, duration: 0, maxNodes: 18 }
export const STORAGE_KEY_PREFIX = 'globemind:story-graph-layout:v2:'

export const VISIBLE_HANDLE_STYLE = {
  width: 4,
  height: 4,
  border: '0.5px solid #2563eb',
  background: '#ffffff',
  boxShadow: '0 0 0 0.75px rgba(255, 255, 255, 0.98)',
}

export const HIDDEN_HANDLE_STYLE = {
  width: 4,
  height: 4,
  border: '0 solid transparent',
  background: 'transparent',
  boxShadow: 'none',
  opacity: 0,
}

export const EVENT_TYPE_LABELS = {
  diplomacy: '外交',
  trade_conflict: '贸易',
  military: '军事',
  policy_legal: '政策',
  protest_repression: '抗议',
  terrorism_espionage: '安全情报',
  aid_disaster: '援助灾害',
  appointment_leadership: '人事领导',
  human_rights_migration: '人权迁移',
  main_event: '核心事件',
  context_update: '背景进展',
  outcome_reaction: '后续反应',
  market_reaction: '市场反应',
  analysis_context: '分析解读',
  official_update: '官方更新',
  prelude: '事前铺垫',
  video_clip: '视频片段',
  conflict: '冲突主线',
  economic: '经济市场',
  political: '政治支线',
  context: '背景支线',
  macro_event: '大事件',
}

export const EDGE_COLORS = {
  continuation: '#5578a8',
  escalation: '#C47A45',
  resolution: '#1f9fb3',
  'de-escalation': '#35a887',
  gap: '#8390a1',
  pair_sequence: '#4f74aa',
  macro_sequence: '#2f8fd0',
  branch_sequence: '#8b98a7',
  branch: '#B99A45',
  parallel: '#21a8a4',
  diplomacy: '#5578a8',
  market_reaction: '#31a879',
  context: '#7E74A8',
  user: '#806db3',
}

export const EVENT_PALETTES = {
  orange: {
    fill: '#FFFFFF',
    stroke: '#C47A45',
    shadow: 'rgba(148, 91, 49, 0.18)',
  },
  green: {
    fill: '#FFFFFF',
    stroke: '#4F8D73',
    shadow: 'rgba(52, 112, 91, 0.17)',
  },
  yellow: {
    fill: '#FFFFFF',
    stroke: '#B99A45',
    shadow: 'rgba(138, 105, 36, 0.17)',
  },
  purple: {
    fill: '#FFFFFF',
    stroke: '#7E74A8',
    shadow: 'rgba(88, 80, 132, 0.17)',
  },
}

export const storyGraphAdapterConfig = Object.freeze({
  fitViewOptions: FIT_VIEW_OPTIONS,
  storageKeyPrefix: STORAGE_KEY_PREFIX,
})
