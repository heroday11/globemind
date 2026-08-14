export const PRODUCT_DATA_FLOW_VERSION = 'product-data-flow-v1'

function moduleRecord({ id, label, route, input, output, state, boundary }) {
  return Object.freeze({
    version: PRODUCT_DATA_FLOW_VERSION,
    id,
    label,
    route,
    input,
    output,
    state,
    boundary,
  })
}

export const PRODUCT_MODULES = Object.freeze([
  moduleRecord({
    id: 'search',
    label: '数据搜索',
    route: '/data-service/data-search',
    input: '用户显式查询与筛选条件',
    output: '候选结果、查询解释与回执',
    state: 'source_contract_available',
    boundary: '命中只表示查询匹配，不表示报道或主张已经核验。',
  }),
  moduleRecord({
    id: 'article',
    label: '新闻详情与证据面板',
    route: '/data-service/news/:id',
    input: '用户从候选结果选择的单条记录',
    output: '原始记录字段、可用证据定位与显式 unknown',
    state: 'source_contract_available',
    boundary: '详情展示不自动证明来源真实性、事实准确性或许可。',
  }),
  moduleRecord({
    id: 'story_graph',
    label: '故事图谱',
    route: '/data-service/story-graph',
    input: '有界事件、节点、边与抽样元数据',
    output: '抽样关系视图与候选报道线索',
    state: 'sampled_hypothesis_view',
    boundary: '布局、时间邻接、并行和关联信号都不是因果或事实证据。',
  }),
  moduleRecord({
    id: 'evidence',
    label: '证据快照',
    route: '/data-service/news/:id',
    input: '认证用户显式选择的文章修订',
    output: '版本化快照标识与本地一致性摘要',
    state: 'authenticated_manual_capture',
    boundary: '本地摘要不是签名、WORM、来源真值或语义蕴含证明。',
  }),
  moduleRecord({
    id: 'assistant',
    label: '数据助手',
    route: '/data-assistant',
    input: '用户问题及本轮成功工具返回的受界上下文',
    output: '带本轮来源 ID 或显式 unknown 的待复核草稿',
    state: 'review_required',
    boundary: '模型输出不是已批准结论；来源 ID 也不证明事实正确或语义蕴含。',
  }),
  moduleRecord({
    id: 'research_workspace',
    label: '研究工作台',
    route: '/research-workspace',
    input: '显式查询快照、证据 ID、假设与人工决定',
    output: '版本化项目、判断、审阅状态与 reviewed draft',
    state: 'human_review_workflow',
    boundary: '工作流状态不替代研究员、法务或机构签发。',
  }),
  moduleRecord({
    id: 'report_center',
    label: '报告中心',
    route: '/data-service/report-center',
    input: '已认证用户显式保存的助手报告草稿',
    output: '个人工作区中的报告索引与下载入口',
    state: 'saved_draft_index',
    boundary: '保存和下载不等于发布、批准、归档或事实核验。',
  }),
  moduleRecord({
    id: 'reviewed_export',
    label: '研究草稿导出',
    route: '/research-workspace',
    input: '研究项目的版本化 manifest 与审阅状态',
    output: '受界 JSON、Markdown、HTML 或 CSV 草稿',
    state: 'reviewed_draft_only',
    boundary: '导出固定为非发布草稿；引用样式、许可和永久链接仍可能未建立。',
  }),
])

function flowRecord({ id, from, to, handoff, provenanceState }) {
  return Object.freeze({
    version: PRODUCT_DATA_FLOW_VERSION,
    id,
    from,
    to,
    handoff,
    automatic: false,
    provenanceState,
    truthAssurance: 'not_established',
  })
}

export const PRODUCT_TASK_FLOWS = Object.freeze([
  flowRecord({
    id: 'search-open-article',
    from: 'search',
    to: 'article',
    handoff: '用户从候选列表显式打开一条详情；查询回执不会被改写为事实结论。',
    provenanceState: 'explicit_receipt',
  }),
  flowRecord({
    id: 'search-send-assistant',
    from: 'search',
    to: 'assistant',
    handoff: '用户显式把当前查询上下文交给助手；工具结果仍逐项受来源 ID/unknown 门禁。',
    provenanceState: 'explicit_receipt',
  }),
  flowRecord({
    id: 'article-send-assistant',
    from: 'article',
    to: 'assistant',
    handoff: '用户显式发送当前详情与可用证据上下文；缺证据内容保持 unknown。',
    provenanceState: 'explicit_receipt',
  }),
  flowRecord({
    id: 'graph-send-assistant',
    from: 'story_graph',
    to: 'assistant',
    handoff: '用户显式发送抽样图谱上下文；抽样关系不得升级为完整图、因果或事实。',
    provenanceState: 'explicit_receipt',
  }),
  flowRecord({
    id: 'article-capture-evidence',
    from: 'article',
    to: 'evidence',
    handoff: '认证用户显式保存当前文章修订，产生版本化快照标识。',
    provenanceState: 'explicit_snapshot',
  }),
  flowRecord({
    id: 'evidence-enter-workspace',
    from: 'evidence',
    to: 'research_workspace',
    handoff: '研究者显式录入快照 ID、来源和关系类型；不会自动导入或自动支持某个判断。',
    provenanceState: 'manual_entry',
  }),
  flowRecord({
    id: 'search-snapshot-workspace',
    from: 'search',
    to: 'research_workspace',
    handoff: '研究者先显式捕获查询快照，再把其 ID 绑定到项目。',
    provenanceState: 'explicit_snapshot',
  }),
  flowRecord({
    id: 'assistant-save-report',
    from: 'assistant',
    to: 'report_center',
    handoff: '认证用户显式保存模型草稿；保存动作不改变其待复核状态。',
    provenanceState: 'required_review',
  }),
  flowRecord({
    id: 'workspace-export-draft',
    from: 'research_workspace',
    to: 'reviewed_export',
    handoff: '研究者在项目内显式生成带审阅状态和水印的 reviewed draft。',
    provenanceState: 'required_review',
  }),
])

const UNAVAILABLE_PRODUCT_MODULE = moduleRecord({
  id: 'unavailable',
  label: '模块未知',
  route: '/unavailable',
  input: 'unavailable',
  output: 'unavailable',
  state: 'unavailable',
  boundary: '模块契约不可用；不得推断输入、输出或自动数据流。',
})

export function productModuleById(moduleId) {
  return PRODUCT_MODULES.find((item) => item.id === moduleId) || UNAVAILABLE_PRODUCT_MODULE
}
