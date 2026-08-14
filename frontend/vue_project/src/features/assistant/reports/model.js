export const REPORT_WORKSPACE_NAME = 'report'
export const REPORT_STORAGE_KEY = 'data_assistant_report_history_v1'

export const REPORT_CONFIG_DEFAULTS = Object.freeze({
  type: 'policy',
  topic: '',
  inputMode: 'prompt',
  customPrompt: '',
  clarificationNotes: '',
  format: 'docx',
  timeRange: '7d',
  perspective: 'analyst',
  template: 'standard',
  includeCharts: true,
  includeSources: true,
  language: 'zh',
})

export const REPORT_TYPES = Object.freeze([
  { key: 'policy', label: '政策分析报告', desc: '政策选项、影响评估、执行建议', icon: 'POL' },
  { key: 'risk', label: '风险评估报告', desc: '风险矩阵、触发条件、缓释方案', icon: 'RSK' },
  { key: 'trend', label: '趋势监测报告', desc: '趋势指标、驱动因素、预警信号', icon: 'TRD' },
  { key: 'event', label: '事件深度分析', desc: '事件链、参与方、因果机制', icon: 'EVT' },
  { key: 'investigation', label: '调查报告', desc: '证据链、矛盾点、待核验问题', icon: 'INV' },
])

export const REPORT_TYPE_PROFILES = Object.freeze({
  policy: {
    focus: '围绕政策背景、利益相关方、政策工具、影响路径、约束条件和执行建议展开。',
    sections: ['执行摘要', '政策背景与问题界定', '关键事实与来源', '利益相关方与政策选项', '影响评估', '执行建议与风险约束'],
    evidence: '区分已发布政策、官方数据、媒体报道和专家判断，建议必须能回扣到事实依据。',
  },
  risk: {
    focus: '围绕风险识别、概率/影响评级、触发条件、情景推演和缓释措施展开。',
    sections: ['核心结论', '风险清单与评级矩阵', '触发信号', '情景推演', '影响对象', '缓释措施与监测指标'],
    evidence: '每项风险标注证据强度、触发阈值和不确定性，不把可能性写成既成事实。',
  },
  trend: {
    focus: '围绕时间序列变化、驱动因素、领先指标、拐点信号和未来观察窗口展开。',
    sections: ['趋势摘要', '指标变化', '主要驱动因素', '区域/主体对比', '未来走势判断', '监测指标与后续检索'],
    evidence: '优先使用可复核的时间、数量、来源和对比口径，说明样本偏差。',
  },
  event: {
    focus: '围绕单一事件的时间线、参与方、因果链、影响扩散和后续演化展开。',
    sections: ['事件概览', '时间线', '参与方与诉求', '因果链分析', '影响评估', '后续演化与核验点'],
    evidence: '时间线必须标注来源或线索，因果判断要区分事实、推断和假设。',
  },
  investigation: {
    focus: '围绕调查问题、事实发现、证据链、矛盾点、来源可信度和待核验线索展开。',
    sections: ['调查摘要', '调查问题与范围', '核心发现', '证据链与来源可信度', '矛盾点/缺口', '涉事主体与关系', '后续核验方向'],
    evidence: '默认交叉核验来源，明确每条关键事实的出处、可靠性、尚未证实部分和下一步验证方法。',
  },
})

export const REPORT_INPUT_MODES = Object.freeze([
  { key: 'brainstorm', label: '需求澄清', desc: '先按 brainstorming 流程提问，明确用途、范围和证据标准' },
  { key: 'prompt', label: '输入提示词', desc: '直接输入主题、提示词和约束条件生成' },
  { key: 'conversation', label: '接入会话', desc: '使用当前会话内容、工具结果和来源线索生成' },
])

export const REPORT_OUTPUT_FORMATS = Object.freeze([
  { key: 'docx', label: 'Word', desc: '生成可下载 .docx 文件', extension: 'docx' },
  { key: 'md', label: 'Markdown', desc: '生成可编辑 .md 文件', extension: 'md' },
  { key: 'pptx', label: 'PPT任务包', desc: '保存 Word 源稿和 ppt-master 任务说明，不直接导出 PPTX', extension: 'pptx' },
])

export const REPORT_BRIEFING_QUESTIONS = Object.freeze([
  '这份报告要支持什么决策或行动？',
  '读者是谁，需要偏事实简报、研判建议还是证据核验？',
  '时间范围、地区范围、对象范围是否有硬边界？',
  '哪些事实或来源必须纳入，哪些来源不能使用？',
  '最需要回答的 3 个问题是什么？',
  '输出篇幅、格式和保密/引用要求是什么？',
])

export const REPORT_TEMPLATES = Object.freeze([
  { key: 'standard', label: '标准模板', desc: '摘要→背景→分析→结论→建议' },
  { key: 'academic', label: '学术模板', desc: '摘要→文献综述→方法论→发现→讨论' },
  { key: 'brief', label: '简报模板', desc: '核心要点→关键数据→行动建议' },
  { key: 'exec', label: '高管摘要', desc: '一页纸结论+支撑附录' },
])

export function createReportConfig(overrides = {}) {
  return { ...REPORT_CONFIG_DEFAULTS, ...overrides }
}

export function reportTypeLabel(type) {
  return REPORT_TYPES.find((item) => item.key === type)?.label || '分析报告'
}

export function reportTemplateLabel(template) {
  return REPORT_TEMPLATES.find((item) => item.key === template)?.label || '标准模板'
}

export function reportFormatLabel(format) {
  return REPORT_OUTPUT_FORMATS.find((item) => item.key === format)?.label || 'Markdown'
}

export function reportTypeProfile(type) {
  return REPORT_TYPE_PROFILES[type] || REPORT_TYPE_PROFILES.policy
}

export function reportPerspectiveLabel(value) {
  const labels = {
    analyst: '政策分析师',
    researcher: '科研人员',
    journalist: '媒体记者',
    executive: '管理决策者',
  }
  return labels[value] || value || '政策分析师'
}

export function reportTimeRangeLabel(value) {
  const labels = {
    '24h': '最近 24 小时',
    '3d': '最近 3 天',
    '7d': '最近 7 天',
    '30d': '最近 30 天',
    custom: '自定义',
  }
  return labels[value] || value || '最近 7 天'
}

export function selectedReportFormat(format) {
  return REPORT_OUTPUT_FORMATS.find((item) => item.key === format) || REPORT_OUTPUT_FORMATS[0]
}

export function normalizeReportRecord(record, options = {}) {
  if (!record || typeof record !== 'object') return null
  const now = typeof options?.now === 'function' ? options.now : () => new Date().toISOString()
  const workspaceName = options?.workspaceName || REPORT_WORKSPACE_NAME
  const topic = String(record.topic || record.title || '').trim()
  const createdAt = record.createdAt || record.time || now()
  const id = record.id || `report-${createdAt}-${topic}`
  return {
    ...record,
    id,
    type: record.type || 'risk',
    format: record.format || (String(record.fileName || '').toLowerCase().endsWith('.docx') ? 'docx' : 'md'),
    inputMode: record.inputMode || 'prompt',
    topic: topic || '未命名主题',
    title: topic || '未命名主题',
    timeRange: record.timeRange || '7d',
    perspective: record.perspective || 'analyst',
    template: record.template || 'standard',
    content: record.content || '',
    createdAt,
    time: createdAt,
    status: record.status || (record.content ? 'done' : 'draft'),
    workspace: record.workspace || workspaceName,
    fileName: record.fileName || '',
    filePath: record.filePath || (record.fileName ? `${workspaceName}/${record.fileName}` : ''),
    sourceFileName: record.sourceFileName || '',
    pptRequestFileName: record.pptRequestFileName || '',
  }
}

export function loadReportHistory(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(REPORT_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.map((record) => normalizeReportRecord(record)).filter(Boolean)
  } catch {
    return []
  }
}

export function saveReportHistory(history, storage = globalThis.localStorage) {
  const rows = (Array.isArray(history) ? history : [])
    .map((record) => normalizeReportRecord(record))
    .filter(Boolean)
  storage?.setItem(REPORT_STORAGE_KEY, JSON.stringify(rows))
}

export function deriveReportTopic({
  config,
  conversations = [],
  currentConversationId = '',
  messages = [],
}) {
  const manual = String(config?.topic || '').trim()
  if (manual) return manual
  const prompt = String(config?.customPrompt || '').trim()
  if (prompt) return prompt.replace(/\s+/g, ' ').slice(0, 36)
  const currentId = String(currentConversationId ?? '').trim()
  const conversation = conversations.find(
    (item) => String(item?.id ?? '').trim() === currentId,
  )
  const title = String(conversation?.title || '').trim()
  if (config?.inputMode === 'conversation' && title && title !== '新会话') {
    return title.slice(0, 36)
  }
  if (config?.inputMode === 'conversation') {
    const firstUserMessage = messages.find(
      (item) => item?.role === 'user' && String(item?.text || '').trim(),
    )
    const text = String(firstUserMessage?.text || '').replace(/\s+/g, ' ').trim()
    if (text) return text.slice(0, 36)
  }
  return ''
}

export function calculateReportEditorStats(content) {
  const text = String(content || '')
  return {
    chars: text.length,
    lines: text ? text.split(/\r\n|\r|\n/).length : 0,
  }
}

export function sanitizeReportFilename(raw) {
  const base = String(raw || 'report')
    .trim()
    .replace(/[\\/:*?"<>|#%{}^~[\]`]+/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 72)
  return base || 'report'
}

export function buildReportFilename(
  topic,
  createdAt = new Date().toISOString(),
  extension = 'md',
) {
  const stamp = new Date(createdAt).toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const ext = String(extension || 'md').replace(/^\./, '').toLowerCase() || 'md'
  return `${stamp}-${sanitizeReportFilename(topic)}.${ext}`
}

export function compactReportContextList(items, limit = 8) {
  return (Array.isArray(items) ? items : [])
    .slice(0, limit)
    .map((item, index) => `${index + 1}. ${item.title || item.name || item.id || '未命名'}${item.source ? `（${item.source}）` : ''}`)
    .join('\n')
}

export function compactReportMessage(message, index, toolName = (name) => name) {
  const role = message?.role === 'assistant' ? '助手' : '用户'
  const text = String(message?.storedFullReply || message?.text || '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, message?.role === 'assistant' ? 900 : 500)
  const toolLines = Array.isArray(message?.toolCalls) && message.toolCalls.length
    ? `；工具：${message.toolCalls.slice(-4).map((tool) => toolName(tool.name || tool.tool || 'tool')).join('、')}`
    : ''
  const sourceLines = Array.isArray(message?.sources) && message.sources.length
    ? `；来源组：${message.sources.slice(0, 4).map((group) => group.title || group.kind || 'source').join('、')}`
    : ''
  return `${index + 1}. ${role}: ${text}${toolLines}${sourceLines}`
}

export function buildConversationReportContext({
  messages = [],
  limit = 12,
  activeConversationTitle = '',
  toolName,
}) {
  const rows = messages
    .slice(-limit)
    .map((message, index) => compactReportMessage(message, index, toolName))
    .filter(Boolean)
  if (!rows.length) return ''
  return [`当前会话：${activeConversationTitle}`, rows.join('\n')].join('\n')
}

export function buildReportPreparationBlock(config, conversationContext = '') {
  if (config?.inputMode === 'brainstorm') {
    const notes = String(config.clarificationNotes || '').trim()
    return [
      '生成前准备：用户选择了 brainstorming 式需求澄清。',
      '澄清问题：',
      REPORT_BRIEFING_QUESTIONS.map((question, index) => `${index + 1}. ${question}`).join('\n'),
      notes ? `用户澄清记录：\n${notes}` : '用户尚未填写完整澄清记录，请根据主题合理收束范围，并在报告中标注仍需确认的问题。',
    ].join('\n')
  }
  if (config?.inputMode === 'conversation') {
    const extra = String(config.customPrompt || '').trim()
    return [
      '生成前准备：用户选择接入当前会话内容。',
      conversationContext ? `会话材料摘要：\n${conversationContext}` : '当前会话暂无可用材料。',
      extra ? `用户补充要求：\n${extra}` : '',
      '请把会话中的判断、工具结果和来源线索作为材料，不要把聊天过程本身写成报告正文。',
    ].join('\n')
  }
  const customPrompt = String(config?.customPrompt || '').trim()
  return [
    '生成前准备：用户选择输入提示词。',
    customPrompt ? `用户提示词：\n${customPrompt}` : '用户未提供额外提示词，请根据报告主题生成。',
  ].join('\n')
}

export function buildReportPrompt({
  config,
  topic,
  format = selectedReportFormat(config?.format),
  skills = [],
  databases = [],
  pinnedFavoriteFolder = '',
  favoriteItems = [],
  reportPanelTopic = '',
  conversationContext = '',
  displayFolderName = (value) => value,
}) {
  const profile = reportTypeProfile(config?.type)
  const favoriteLines = favoriteItems.length
    ? compactReportContextList(favoriteItems, 12)
    : ''

  return [
    `请以 GlobeMind 数据助手身份生成一份可存档的 Markdown 报告草稿，系统会在保存阶段导出为 ${reportFormatLabel(format.key)}。`,
    '',
    `报告主题：${topic}`,
    `报告类型：${reportTypeLabel(config?.type)}`,
    `类型差异要求：${profile.focus}`,
    `建议章节：${profile.sections.map((item, index) => `${index + 1}. ${item}`).join('；')}`,
    `证据标准：${profile.evidence}`,
    `输出格式：${format.label}${format.key === 'docx' ? '（保存时转换为 .docx）' : format.key === 'md' ? '（保存为 .md）' : format.key === 'pptx' ? '（先保存 Word 源稿，再创建 ppt-master 任务）' : ''}`,
    `时间范围：${reportTimeRangeLabel(config?.timeRange)}`,
    `分析视角：${reportPerspectiveLabel(config?.perspective)}`,
    `输出模板：${reportTemplateLabel(config?.template)}`,
    `图表要求：${config?.includeCharts ? '需要给出可转成图表的数据点、指标或表格' : '不强制生成图表'}`,
    `引用要求：${config?.includeSources ? '需要列出证据来源、新闻 ID、检索线索或资料路径' : '只需保留关键证据摘要'}`,
    '',
    skills.length ? `已启用 Skill：${skills.join('、')}` : '当前未显式选择 Skill，请使用平台默认新闻检索、事件聚类、知识库和舆情工具。',
    databases.length ? `可参考数据库卡片：${databases.join('、')}` : '',
    pinnedFavoriteFolder ? `已固定收藏文件夹：${displayFolderName(pinnedFavoriteFolder)}` : '',
    favoriteLines ? `固定收藏材料：\n${favoriteLines}` : '',
    reportPanelTopic ? `报告中心上下文主题：${reportPanelTopic}` : '',
    buildReportPreparationBlock(config, conversationContext),
    '',
    '写作要求：',
    '1. 使用 Markdown，标题层级清楚，默认信息密度高、事实密集、结论靠前。',
    '2. 信息来源必须真实、可检索、可复核；优先使用平台检索、已选 Skill、收藏材料、知识库、事件数据和会话内工具结果。',
    '3. 不输出“...”或“待补充”等占位内容；证据不足时明确列出证据缺口、可信度和下一步核验方式。',
    '4. 分析要区分事实、推断、判断和建议，避免把未经证实的信息写成确定事实。',
    '5. 如包含表格，请使用标准 Markdown 表格，便于导出 Word。',
    format.key === 'pptx' ? '6. 这份内容将先作为 Word 源稿，再交给 ppt-master 生成 PPT；请保证正文结构、标题层级和关键图表线索足够清晰，便于后续拆分为幻灯片。' : '',
    '7. 结尾给出适合保存到 report 文件夹的文件标题。',
  ].filter(Boolean).join('\n')
}

export function buildPptMasterRequestMarkdown({
  topic,
  createdAt,
  wordFilename,
  content,
  workspaceName = REPORT_WORKSPACE_NAME,
  formatDateTime = (value) => value,
}) {
  const sourcePath = `${workspaceName}/${wordFilename}`
  return [
    `# ppt-master 生成任务：${topic}`,
    '',
    '## Source Document',
    `- Word 源稿：${sourcePath}`,
    `- 创建时间：${formatDateTime(createdAt)}`,
    '- 生成约束：先以 Word 源稿作为唯一主输入，再进入 ppt-master 流程生成 PPTX。',
    '',
    '## Required Workflow',
    '1. 使用 ppt-master skill。',
    '2. Step 1 读取 Word 源稿；如需转换，使用 doc_to_md.py 得到 Markdown 内容。',
    '3. Step 2 创建 ppt-master project，导入源文档。',
    '4. Step 4 按 ppt-master 要求完成 Eight Confirmations；未经确认不得跳过策略阶段。',
    '5. 后续按 spec_lock 顺序生成 SVG 页面、质检、finalize 并导出 PPTX。',
    '',
    '## Deck Requirements',
    '- 默认 16:9 PPT。',
    '- 以报告主线拆分为封面、摘要、关键事实、证据链/图表、判断与建议、附录/来源。',
    '- 每页只保留一个主结论，来源和不确定性在备注或页脚中标注。',
    '- 保持信息密度高，但避免把 Word 段落直接铺满页面。',
    '',
    '## Word Source Snapshot',
    '```markdown',
    String(content || '').trim(),
    '```',
  ].join('\n')
}
