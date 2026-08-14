import fs from 'fs';

let c = fs.readFileSync('src/views/DataAssistant.vue', 'utf8');

// ============================================================
// 1. SCRIPT SECTION — functional changes only
// ============================================================

// 1a. Add 'watch' to imports
c = c.replace(
  "import { computed, ref, onMounted, onUnmounted, reactive, nextTick } from 'vue'",
  "import { computed, ref, onMounted, onUnmounted, reactive, nextTick, watch } from 'vue'"
);

// 1b. Add history tab to topTabs (history between briefing and agentReport)
c = c.replace(
  "const topTabs = [\n    { key: 'briefing', label: '定期简报' },\n    { key: 'agentReport', label: '智能体报告' },\n  ]",
  "const topTabs = [\n    { key: 'briefing', label: '定期简报' },\n    { key: 'history', label: '历史会话' },\n    { key: 'agentReport', label: '智能体报告' },\n  ]"
);

// 1c. Add composerTextareaEl ref
c = c.replace(
  "const briefingDraft = ref('')\nconst chatScrollBriefingEl = ref(null)",
  "const briefingDraft = ref('')\nconst composerTextareaEl = ref(null)\nconst chatScrollBriefingEl = ref(null)"
);

// 1d. Add hasConversation and hasStreamContent after messages ref
c = c.replace(
  "const messages = ref([])\nconst newsHits = ref([])",
  "const messages = ref([])\nconst hasConversation = computed(() => messages.value.length > 0)\nconst hasStreamContent = computed(() => {\n  if (!streaming.value) return false\n  const last = messages.value[messages.value.length - 1]\n  return last?.role === 'assistant' && last.text?.length > 0\n})\nconst newsHits = ref([])"
);

// 1e. Add helper functions before runCapability
c = c.replace(
  "function runCapability(query) {\n  inputText.value = query\n  sendMessage()\n}",
  "function formatConvTime(ts) {\n  if (!ts) return ''\n  const now = Date.now()\n  const diff = now - ts\n  if (diff < 60000) return '刚刚'\n  if (diff < 3600000) return Math.floor(diff / 60000) + ' 分钟前'\n  if (diff < 86400000) return Math.floor(diff / 3600000) + ' 小时前'\n  if (diff < 604800000) return Math.floor(diff / 86400000) + ' 天前'\n  return new Date(ts).toLocaleDateString('zh-CN')\n}\n\nasync function startNewConversation() {\n  await createConversation()\n  scrollChatToBottom()\n}\n\nasync function openHistoryConversation(id) {\n  await selectConversation(id)\n  scrollChatToBottom()\n}\n\nfunction autoSelectLatestConversation() {\n  if (conversations.value.length > 0 && !currentConversationId.value) {\n    const latest = conversations.value.reduce((a, b) => (a.at > b.at ? a : b))\n    openHistoryConversation(latest.id)\n  }\n}\n\nfunction autoResizeComposer() {\n  const el = composerTextareaEl.value\n  if (!el) return\n  el.style.height = '48px'\n  el.style.height = Math.min(el.scrollHeight, 120) + 'px'\n}\n\nfunction runCapability(query) {\n  inputText.value = query\n  sendMessage()\n}"
);

// 1f. Add watch before onMounted
c = c.replace(
  "\nonMounted(async () => {",
  "\nwatch(activeTopTab, (tab) => {\n  if (tab === 'history') {\n    autoSelectLatestConversation()\n  }\n})\n\nonMounted(async () => {"
);

// ============================================================
// 2. TEMPLATE SECTION
// ============================================================

// 2a. Update topbar - add conversation class and GlobeMind brand
c = c.replace(
  '<header class="ys-topbar" aria-label="顶部栏">\n          <div class="ys-tabs"',
  '<header class="ys-topbar" :class="{ \'ys-topbar--conversation\': hasConversation && activeTopTab === \'briefing\' }" aria-label="顶部栏">\n          <div class="ys-tabs"'
);

c = c.replace(
  '          </div>\n          <div class="ys-topbar-right">',
  '          </div>\n          <div v-if="hasConversation && activeTopTab === \'briefing\'" class="ys-topbar-brand">GlobeMind</div>\n          <div class="ys-topbar-right">'
);

// 2b. Add history panel after taskpanel </aside>
c = c.replace(
  '        </aside>\n\n        <main class="ys-main">',
  '        </aside>\n\n        <aside v-if="activeTopTab === \'history\'" class="ys-taskpanel" aria-label="历史会话列表">\n          <button type="button" class="ys-task-new ys-history-start-btn" @click="startNewConversation">\n            + 发起新对话\n          </button>\n          <div v-if="conversations.length === 0" class="ys-history-panel-empty">\n            暂无历史会话\n          </div>\n          <div v-else class="ys-task-list">\n            <button\n              v-for="conv in conversations"\n              :key="conv.id"\n              type="button"\n              class="ys-task-card ys-history-session-card"\n              :class="{ \'ys-history-session-card--active\': String(conv.id) === String(currentConversationId) }"\n              @click="openHistoryConversation(conv.id)"\n            >\n              <div class="ys-task-card-top">\n                <span class="ys-task-pill">会话</span>\n                <span class="ys-task-stats">{{ formatConvTime(conv.at) }}</span>\n              </div>\n              <div class="ys-task-title">{{ conv.title || \'会话\' }}</div>\n              <div class="ys-task-meta">ID: {{ conv.id }}</div>\n            </button>\n          </div>\n        </aside>\n\n        <main class="ys-main">'
);

// 2c. Update briefing - hero/cards conditional, chat with expand/composer
c = c.replace(
  '<div v-else-if="activeTopTab === \'briefing\'" class="ys-briefing">\n            <div class="ys-hero">\n              <h1 class="ys-hero-title">学者您好，欢迎来到GlobeMind</h1>\n              <span class="ys-hero-badge">定期简报</span>\n              <p class="ys-hero-sub">\n                快速追踪、自动总结与推送，帮助您高效把握最新动向。\n              </p>\n            </div>\n\n            <div class="ys-cardgrid" aria-label="能力卡片">\n              <div class="ys-card">\n                <div class="ys-card-icon">✎</div>\n                <div class="ys-card-title">多源背景，一网打尽</div>\n                <div class="ys-card-desc">覆盖多平台资料采集与结构化整理，聚合关键线索。</div>\n              </div>\n              <div class="ys-card">\n                <div class="ys-card-icon">◇</div>\n                <div class="ys-card-title">AI智能体，定期代笔</div>\n                <div class="ys-card-desc">按模板自动生成简报/报告，支持多角色视角与引用。</div>\n              </div>\n              <div class="ys-card">\n                <div class="ys-card-icon">◎</div>\n                <div class="ys-card-title">聚焦热点，高效监测</div>\n                <div class="ys-card-desc">自研算法提炼热点簇，形成时间线与跟进建议。</div>\n              </div>\n            </div>',
  '<div v-else-if="activeTopTab === \'briefing\'" class="ys-briefing" :class="{ \'ys-briefing--conversation\': hasConversation }">\n            <template v-if="!hasConversation">\n            <div class="ys-hero">\n              <h1 class="ys-hero-title">学者您好，欢迎来到GlobeMind</h1>\n              <span class="ys-hero-badge">定期简报</span>\n              <p class="ys-hero-sub">\n                快速追踪、自动总结与推送，帮助您高效把握最新动向。\n              </p>\n            </div>\n\n            <div class="ys-cardgrid" aria-label="能力卡片">\n              <div class="ys-card">\n                <div class="ys-card-icon">✎</div>\n                <div class="ys-card-title">多源背景，一网打尽</div>\n                <div class="ys-card-desc">覆盖多平台资料采集与结构化整理，聚合关键线索。</div>\n              </div>\n              <div class="ys-card">\n                <div class="ys-card-icon">◇</div>\n                <div class="ys-card-title">AI智能体，定期代笔</div>\n                <div class="ys-card-desc">按模板自动生成简报/报告，支持多角色视角与引用。</div>\n              </div>\n              <div class="ys-card">\n                <div class="ys-card-icon">◎</div>\n                <div class="ys-card-title">聚焦热点，高效监测</div>\n                <div class="ys-card-desc">自研算法提炼热点簇，形成时间线与跟进建议。</div>\n              </div>\n            </div>\n            </template>'
);

// 2d. Update chat section
c = c.replace(
  '<section class="ys-briefing-chat" aria-label="定期简报对话">\n              <div class="ys-briefing-chat-head">\n                <div class="ys-briefing-chat-title">AI 对话</div>\n                <div class="ys-briefing-chat-meta">支持 Shift + Enter 换行，Enter 发送</div>\n              </div>\n              <div ref="chatScrollBriefingEl" class="ys-briefing-chat-scroll">\n                <div v-if="messages.length === 0" class="ys-briefing-chat-empty">\n                  你想了解什么？可以从 "美伊冲突升级风险" 或 "红海航运安全" 开始问。\n                </div>\n                <div v-else class="ys-briefing-chat-list">\n                  <div v-for="(m, idx) in messages" :key="messageKey(m, idx)" class="ys-bmsg" :class="\'ys-bmsg--\' + m.role">\n                    <div class="ys-bmsg-bubble">{{ m.text }}</div>\n                  </div>\n                  <div v-if="sending && !streaming" class="ys-bmsg ys-bmsg--assistant">\n                    <div class="ys-bmsg-bubble ys-bmsg-bubble--typing">思考中…</div>\n                  </div>\n                </div>\n              </div>\n              <div class="ys-briefing-composer">\n                <textarea\n                  v-model="briefingDraft"\n                  rows="1"\n                  class="ys-briefing-input"\n                  placeholder="输入消息…"\n                  @keydown.enter.exact.prevent="sendBriefingMessage"\n                />\n                <button type="button" class="ys-briefing-send" :disabled="sending || !briefingDraft.trim()" @click="sendBriefingMessage">\n                  发送\n                </button>\n              </div>\n            </section>',
  '<section class="ys-briefing-chat" :class="{ \'ys-briefing-chat--expanded\': hasConversation }" aria-label="定期简报对话">\n              <div v-if="!hasConversation" class="ys-briefing-chat-head">\n                <div class="ys-briefing-chat-title">AI 对话</div>\n                <div class="ys-briefing-chat-meta">支持 Shift + Enter 换行，Enter 发送</div>\n              </div>\n              <div ref="chatScrollBriefingEl" class="ys-briefing-chat-scroll" :class="{ \'ys-briefing-chat-scroll--expanded\': hasConversation }">\n                <div v-if="messages.length === 0" class="ys-briefing-chat-empty">\n                  你想了解什么？可以从 "美伊冲突升级风险" 或 "红海航运安全" 开始问。\n                </div>\n                <div v-else class="ys-briefing-chat-list">\n                  <div v-for="(m, idx) in messages" :key="messageKey(m, idx)" class="ys-bmsg" :class="\'ys-bmsg--\' + m.role">\n                    <div v-if="m.text" class="ys-bmsg-bubble">{{ m.text }}</div>\n                  </div>\n                  <div v-if="sending && !hasStreamContent" class="ys-bmsg ys-bmsg--assistant">\n                    <div class="ys-bmsg-bubble ys-bmsg-bubble--typing">思考中…</div>\n                  </div>\n                </div>\n              </div>\n              <div class="ys-briefing-composer">\n                <div class="ys-composer-capsule">\n                  <div class="ys-composer-input-area">\n                    <textarea\n                      ref="composerTextareaEl"\n                      v-model="briefingDraft"\n                      rows="1"\n                      class="ys-briefing-input"\n                      placeholder="输入消息…"\n                      @keydown.enter.exact.prevent="sendBriefingMessage"\n                      @input="autoResizeComposer"\n                    />\n                  </div>\n                  <div class="ys-composer-action-bar">\n                    <button type="button" class="ys-briefing-send" :disabled="sending || !briefingDraft.trim()" @click="sendBriefingMessage">\n                      发送\n                    </button>\n                  </div>\n                </div>\n              </div>\n            </section>'
);

// 2e. agentReport: v-else -> v-else-if
c = c.replace(
  '<div v-else class="ys-agent-report">',
  '<div v-else-if="activeTopTab === \'agentReport\'" class="ys-agent-report">'
);

// 2f. Add history tab content (same as briefing, after agentReport) before </main>
c = c.replace(
  '          </div>\n        </main>',
  '          </div>\n\n          <div v-else class="ys-briefing" :class="{ \'ys-briefing--conversation\': hasConversation }">\n            <template v-if="!hasConversation">\n            <div class="ys-hero">\n              <h1 class="ys-hero-title">学者您好，欢迎来到GlobeMind</h1>\n              <span class="ys-hero-badge">历史会话</span>\n              <p class="ys-hero-sub">\n                从左侧选择一个会话继续对话，或发起新对话开始探索。\n              </p>\n            </div>\n            <div class="ys-cardgrid" aria-label="能力卡片">\n              <div class="ys-card">\n                <div class="ys-card-icon">✎</div>\n                <div class="ys-card-title">多源背景，一网打尽</div>\n                <div class="ys-card-desc">覆盖多平台资料采集与结构化整理，聚合关键线索。</div>\n              </div>\n              <div class="ys-card">\n                <div class="ys-card-icon">◇</div>\n                <div class="ys-card-title">AI智能体，定期代笔</div>\n                <div class="ys-card-desc">按模板自动生成简报/报告，支持多角色视角与引用。</div>\n              </div>\n              <div class="ys-card">\n                <div class="ys-card-icon">◎</div>\n                <div class="ys-card-title">聚焦热点，高效监测</div>\n                <div class="ys-card-desc">自研算法提炼热点簇，形成时间线与跟进建议。</div>\n              </div>\n            </div>\n            </template>\n            <section class="ys-briefing-chat" :class="{ \'ys-briefing-chat--expanded\': hasConversation }" aria-label="历史会话对话">\n              <div v-if="!hasConversation" class="ys-briefing-chat-head">\n                <div class="ys-briefing-chat-title">AI 对话</div>\n                <div class="ys-briefing-chat-meta">支持 Shift + Enter 换行，Enter 发送</div>\n              </div>\n              <div ref="chatScrollBriefingEl" class="ys-briefing-chat-scroll" :class="{ \'ys-briefing-chat-scroll--expanded\': hasConversation }">\n                <div v-if="messages.length === 0" class="ys-briefing-chat-empty">\n                  你想了解什么？可以从 "美伊冲突升级风险" 或 "红海航运安全" 开始问。\n                </div>\n                <div v-else class="ys-briefing-chat-list">\n                  <div v-for="(m, idx) in messages" :key="messageKey(m, idx)" class="ys-bmsg" :class="\'ys-bmsg--\' + m.role">\n                    <div v-if="m.text" class="ys-bmsg-bubble">{{ m.text }}</div>\n                  </div>\n                  <div v-if="sending && !hasStreamContent" class="ys-bmsg ys-bmsg--assistant">\n                    <div class="ys-bmsg-bubble ys-bmsg-bubble--typing">思考中…</div>\n                  </div>\n                </div>\n              </div>\n              <div class="ys-briefing-composer">\n                <div class="ys-composer-capsule">\n                  <div class="ys-composer-input-area">\n                    <textarea\n                      ref="composerTextareaEl"\n                      v-model="briefingDraft"\n                      rows="1"\n                      class="ys-briefing-input"\n                      placeholder="输入消息…"\n                      @keydown.enter.exact.prevent="sendBriefingMessage"\n                      @input="autoResizeComposer"\n                    />\n                  </div>\n                  <div class="ys-composer-action-bar">\n                    <button type="button" class="ys-briefing-send" :disabled="sending || !briefingDraft.trim()" @click="sendBriefingMessage">\n                      发送\n                    </button>\n                  </div>\n                </div>\n              </div>\n            </section>\n          </div>\n        </main>'
);

// ============================================================
// 3. CSS — only new functional classes, no visual redesign
// ============================================================
const newCSS = `

/* ---- 胶囊输入框 ---- */
.ys-briefing-composer {
  padding: 0 24px 36px;
  background: transparent;
  border-top: none;
  width: 100%;
  max-width: 960px;
  margin: 0 auto;
  box-sizing: border-box;
}

.ys-composer-capsule {
  display: flex;
  flex-direction: column;
  border-radius: 20px;
  border: 1px solid #e0e0e0;
  background-color: #ffffff;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.ys-composer-capsule:focus-within {
  border-color: #519bf1;
  box-shadow: 0 2px 15px rgba(81, 155, 241, 0.15);
}

.ys-composer-input-area {
  flex: 0 1 auto;
  min-height: 48px;
}

.ys-composer-action-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 6px 12px 10px 16px;
  background: #ffffff;
  flex-shrink: 0;
}

/* ---- 对话全屏展开 ---- */
.ys-briefing--conversation {
  padding: 0;
  height: 100%;
  overflow: hidden;
}

.ys-briefing-chat--expanded {
  margin-top: 0;
  border-radius: 0;
  border: none;
  box-shadow: none;
  background: transparent;
  flex: 1;
  min-height: 0;
}

.ys-briefing-chat-scroll--expanded {
  max-height: none;
  min-height: 0;
  flex: 1;
  padding: 20px 24px;
}

/* ---- 顶部栏对话模式 ---- */
.ys-topbar--conversation {
  border-bottom: 1px solid rgba(15, 23, 42, 0.06);
}

.ys-topbar-brand {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  font-size: 16px;
  font-weight: 700;
  color: rgba(15, 23, 42, 0.78);
  letter-spacing: -0.01em;
}

/* ---- 历史会话面板 ---- */
.ys-history-start-btn {
  border-color: rgba(59, 130, 246, 0.35);
  background: rgba(59, 130, 246, 0.1);
  color: #2563eb;
}

.ys-history-start-btn:hover {
  background: #3b82f6;
  color: #fff;
}

.ys-history-panel-empty {
  text-align: center;
  padding: 32px 12px;
  font-size: 13px;
  color: #94a3b8;
}

.ys-history-session-card--active {
  border-color: rgba(59, 130, 246, 0.35);
  background: rgba(59, 130, 246, 0.06);
}
`;

const styleEnd = c.lastIndexOf('</style>');
c = c.substring(0, styleEnd) + newCSS + '\n</style>';

fs.writeFileSync('src/views/DataAssistant.vue', c);
console.log('Done - functional changes only, no visual redesign');
