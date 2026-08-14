<template>
  <div class="knowledge-vault-page">
    <aside class="vault-sidebar" data-tour="knowledge-navigation">
      <div class="vault-brand">
        <div class="vault-brand-mark">
          <Archive />
        </div>
        <div>
          <p class="vault-eyebrow">GlobeMind Knowledge Vault</p>
          <h1>Agent 能力与连接卡</h1>
        </div>
      </div>

      <label class="vault-search">
        <Search />
        <input v-model="searchKeyword" type="search" placeholder="检索领域、skill、任务或来源" />
      </label>

      <nav class="domain-nav" aria-label="专家领域">
        <button
          class="domain-nav-item"
          :class="{ active: activeDomainId === 'all' }"
          type="button"
          @click="activeDomainId = 'all'"
        >
          <Layers />
          <span>全部领域</span>
          <strong>{{ totalSkills }}</strong>
        </button>

        <button
          v-for="domain in domains"
          :key="domain.id"
          class="domain-nav-item"
          :class="{ active: activeDomainId === domain.id }"
          type="button"
          @click="activeDomainId = domain.id"
        >
          <component :is="domainIcon(domain.id)" />
          <span>{{ domain.shortName || domain.name }}</span>
          <strong>{{ domain.skills?.length || 0 }}</strong>
        </button>
      </nav>

      <div class="source-shelf">
        <div class="side-section-title">
          <DownloadCloud />
          <span>已下载索引</span>
        </div>
        <button
          v-for="source in downloadedSources"
          :key="source.id"
          type="button"
          class="source-row"
          @click="openLocalSource(source)"
        >
          <span>{{ source.publisher }}</span>
          <strong>{{ source.name }}</strong>
        </button>
      </div>
    </aside>

    <main class="vault-main">
      <aside class="route-meaning-notice" aria-labelledby="route-meaning-title">
        <div>
          <strong id="route-meaning-title">旧路径说明</strong>
          <p>
            为兼容书签，本页仍使用 <code>/academic-data</code> 路径；这里是 Agent Skill
            与本地连接说明市场，不是论文、书目或已接通的学术数据库。
          </p>
        </div>
        <router-link to="/sources">查看已登记的数据来源与许可状态</router-link>
      </aside>

      <section class="vault-hero">
        <div class="hero-copy">
          <div class="hero-kicker">
            <ShieldCheck />
            <span>给 Agent 装配可审查的专家能力与数据源</span>
          </div>
          <h2>Skill 与数据库市场</h2>
          <p>
            选择专家 skill 并登记数据库连接说明；是否实际接通、来源许可与可用范围以数据目录和页内状态为准。
          </p>
        </div>

        <div class="hero-ledger" aria-label="知识库统计">
          <div class="ledger-cell">
            <span>{{ catalogLoading ? '—' : domains.length }}</span>
            <p>专家领域</p>
          </div>
          <div class="ledger-cell">
            <span>{{ catalogLoading ? '—' : totalSkills }}</span>
            <p>已整理 skill</p>
          </div>
          <div class="ledger-cell">
            <span>{{ catalogLoading ? '—' : downloadedSources.length }}</span>
            <p>下载索引</p>
          </div>
          <div class="ledger-cell">
            <span>{{ databaseCards.length }}</span>
            <p>数据库卡片</p>
          </div>
        </div>
      </section>

      <section class="vault-grid">
        <div class="skill-library" data-tour="knowledge-skills">
          <header class="panel-header">
            <div>
              <p class="section-label">Expert Skill Library</p>
              <h3>{{ activeDomainTitle }}</h3>
            </div>
            <div class="panel-actions">
              <span class="sync-pill">
                <RefreshCw />
                {{ catalogMeta }}
              </span>
            </div>
          </header>

          <div v-if="activeDomain && activeDomainId !== 'all'" class="domain-brief">
            <div class="domain-brief-icon">
              <component :is="domainIcon(activeDomain.id)" />
            </div>
            <div>
              <strong>{{ activeDomain.description }}</strong>
              <p>
                推荐存储：{{ activeDomain.recommendedStore }} · 风险等级：{{ activeDomain.riskLevel }}
              </p>
            </div>
          </div>

          <div v-if="catalogLoading" class="empty-state catalog-state" role="status">
            <RefreshCw class="catalog-spinner" />
            <strong>正在加载 Skill 目录</strong>
            <p>目录较大，请稍候；加载完成前不会显示为 0 个 skill。</p>
          </div>

          <div v-else-if="catalogError" class="empty-state catalog-state" role="alert">
            <BookOpen />
            <strong>Skill 目录加载失败</strong>
            <p>{{ catalogError }}</p>
            <button type="button" class="use-skill-btn" @click="loadCatalog">重新加载</button>
          </div>

          <div v-else-if="filteredSkills.length" class="skill-grid">
            <article
              v-for="skill in filteredSkills"
              :key="skill.id"
              class="skill-card"
              :class="{ selected: isSkillSelected(skill.id) }"
              role="button"
              tabindex="0"
              @click="openSkillDetail(skill)"
              @keydown.enter.prevent="openSkillDetail(skill)"
              @keydown.space.prevent="openSkillDetail(skill)"
            >
              <div class="skill-card-accent" aria-hidden="true">
                <span>SKILL</span>
                <strong>{{ skillTasks(skill).length }}</strong>
              </div>
              <div class="skill-card-badges">
                <span class="status-badge" :class="statusClass(skill.status)">{{ statusLabel(skill.status) }}</span>
                <span class="license-badge">{{ skill.license || 'License N/A' }}</span>
                <span v-if="skill.hasSkillMd" class="license-badge">SKILL.md</span>
              </div>
              <div class="skill-card-top">
                <div class="skill-icon">
                  <component :is="domainIcon(skill.domainId)" />
                </div>
                <div>
                  <span class="skill-domain">{{ skill.domainName }}</span>
                  <h4>{{ skillName(skill) }}</h4>
                </div>
              </div>
              <div class="skill-card-highlight">
                <span>{{ skillTasks(skill)[0] || '专家能力' }}</span>
                <strong>{{ skillKnowledgeInputs(skill).length }} 类知识输入</strong>
              </div>
              <p class="skill-desc">{{ skillDescription(skill) }}</p>
              <div class="skill-tags">
                <span v-for="task in skillTasks(skill)" :key="task">{{ task }}</span>
              </div>
              <dl class="skill-meta">
                <div>
                  <dt>来源</dt>
                  <dd>{{ skill.source }}</dd>
                </div>
                <div>
                  <dt>激活条件</dt>
                  <dd>{{ skillActivation(skill) }}</dd>
                </div>
                <div>
                  <dt>运行时</dt>
                  <dd>{{ runtimeLabel(skill.runtime) }}</dd>
                </div>
              </dl>
              <div class="skill-footer">
                <span class="safety-note">{{ skillSafety(skill) }}</span>
                <button class="use-skill-btn" type="button" @click.stop="toggleSkill(skill)">
                  <CheckCircle2 v-if="isSkillSelected(skill.id)" />
                  <Plus v-else />
                  {{ isSkillSelected(skill.id) ? '已选择使用' : '选择使用' }}
                </button>
              </div>
            </article>
          </div>

          <div v-else class="empty-state">
            <BookOpen />
            <strong>没有匹配的 skill</strong>
            <p>换一个关键词或领域筛选。</p>
          </div>
        </div>

        <aside class="database-console">
          <section class="selected-skills-panel">
            <header class="panel-header compact">
              <div>
                <p class="section-label">Active Skills</p>
                <h3>已使用 Skill</h3>
              </div>
              <span class="selected-count">{{ selectedSkills.length }}</span>
            </header>

            <div v-if="selectedSkills.length" class="selected-skill-list">
              <article v-for="skill in selectedSkills" :key="skill.id" class="selected-skill-item">
                <button type="button" class="selected-skill-main" @click="openSkillDetail(skill)">
                  <span class="selected-skill-icon">
                    <component :is="domainIcon(skill.domainId)" />
                  </span>
                  <span>
                    <strong>{{ skillName(skill) }}</strong>
                    <small>{{ skill.domainName }}</small>
                  </span>
                </button>
                <button
                  type="button"
                  class="selected-skill-remove"
                  title="移除 skill"
                  @click="removeSelectedSkill(skill.id)"
                >
                  <X />
                </button>
              </article>
            </div>

            <div v-else class="selected-empty">
              <BrainCircuit />
              <strong>还没有选择 skill</strong>
              <p>在左侧卡片点击“选择使用”，这里会形成当前 Agent 的专家能力列表。</p>
            </div>
          </section>

          <header class="panel-header compact">
            <div>
              <p class="section-label">Database Registry</p>
              <h3>数据库连接卡片</h3>
            </div>
            <Server />
          </header>

          <p class="db-privacy-note">
            <LockKeyhole /> 连接卡仅在当前浏览器保存连接说明，不保存密码，也不会自动连接外部数据库。
          </p>

          <form class="db-form" data-tour="knowledge-databases" @submit.prevent="saveDatabaseCard">
            <label>
              <span>卡片名称</span>
              <input v-model.trim="dbForm.name" type="text" placeholder="例如：地缘事件向量库" />
            </label>

            <div class="form-row">
              <label>
                <span>类型</span>
                <select v-model="dbForm.type" @change="applyDefaultPort">
                  <option v-for="type in dbTypes" :key="type.name" :value="type.name">{{ type.name }}</option>
                </select>
              </label>
              <label>
                <span>端口</span>
                <input v-model.trim="dbForm.port" type="text" inputmode="numeric" placeholder="5432" />
              </label>
            </div>

            <label>
              <span>数据库 IP / Host</span>
              <input v-model.trim="dbForm.host" type="text" placeholder="10.0.0.12 或 db.internal" />
            </label>

            <div class="form-row">
              <label>
                <span>库名 / Collection</span>
                <input v-model.trim="dbForm.database" type="text" placeholder="globemind_kb" />
              </label>
              <label>
                <span>用户名</span>
                <input v-model.trim="dbForm.username" type="text" placeholder="readonly_user" />
              </label>
            </div>

            <label>
              <span>用途</span>
              <textarea v-model.trim="dbForm.purpose" rows="3" placeholder="存放地缘政治报告切片、向量和引用来源"></textarea>
            </label>

            <label>
              <span>标签</span>
              <input v-model.trim="dbForm.tags" type="text" placeholder="RAG, 只读, 生产" />
            </label>

            <p class="db-warning">
              <LockKeyhole />
              不保存密码或 Token。请只填写连接描述，密钥放在后端环境变量或密钥管理系统。
            </p>

            <p v-if="dbFormError" class="form-error">{{ dbFormError }}</p>

            <button class="save-db-btn" type="submit">
              <Plus />
              保存数据库卡片
            </button>
          </form>

          <div class="db-card-list">
            <article v-for="card in databaseCards" :key="card.id" class="db-card">
              <div class="db-card-head">
                <div class="db-type-icon">
                  <Database />
                </div>
                <div>
                  <h4>{{ card.name }}</h4>
                  <p>{{ card.type }} · {{ card.host }}:{{ card.port }}</p>
                </div>
                <button type="button" class="remove-db-btn" title="删除卡片" @click="removeDatabaseCard(card.id)">
                  <X />
                </button>
              </div>
              <dl>
                <div>
                  <dt>库名</dt>
                  <dd>{{ card.database || '未填写' }}</dd>
                </div>
                <div>
                  <dt>用户</dt>
                  <dd>{{ card.username || '未填写' }}</dd>
                </div>
              </dl>
              <p class="db-purpose">{{ card.purpose || '未填写用途说明。' }}</p>
              <div class="db-tags">
                <span v-for="tag in card.tags" :key="tag">{{ tag }}</span>
              </div>
            </article>

            <div v-if="!databaseCards.length" class="db-empty">
              <PlugZap />
              <strong>还没有数据库卡片</strong>
              <p>填写左侧连接信息后，会在这里保存为可管理的知识库数据源。</p>
            </div>
          </div>
        </aside>
      </section>

      <section class="source-panel">
        <header class="panel-header">
          <div>
            <p class="section-label">Downloaded References</p>
            <h3>外部 skill 索引资料</h3>
          </div>
          <span class="source-policy">{{ catalogPolicy.securityNote }}</span>
        </header>
        <div class="source-grid">
          <article v-for="source in downloadedSources" :key="source.id" class="source-card">
            <div class="source-card-head">
              <FileText />
              <span>{{ trustLabel(source.trustLevel) }}</span>
            </div>
            <h4>{{ source.name }}</h4>
            <p>{{ source.notes }}</p>
            <p class="source-license">{{ source.license }}</p>
            <div class="source-links">
              <button type="button" @click="openLocalSource(source)">
                <BookOpen />
                本地索引
              </button>
              <button type="button" @click="openExternalSource(source)">
                <ExternalLink />
                GitHub
              </button>
            </div>
          </article>
          </div>
        </section>
      </main>

      <Transition name="modal-fade">
        <div v-if="detailSkill" class="skill-modal-backdrop" @click.self="closeSkillDetail">
          <section class="skill-modal" role="dialog" aria-modal="true" :aria-label="skillName(detailSkill)">
            <button type="button" class="modal-close" title="关闭" @click="closeSkillDetail">
              <X />
            </button>

            <div class="modal-head">
              <div class="modal-icon">
                <component :is="domainIcon(detailSkill.domainId)" />
              </div>
              <div>
                <span>{{ detailSkill.domainName }}</span>
                <h3>{{ skillName(detailSkill) }}</h3>
                <small v-if="skillOriginalName(detailSkill)" class="modal-original-name">
                  原名：{{ skillOriginalName(detailSkill) }}
                </small>
                <p>{{ skillDescription(detailSkill) }}</p>
              </div>
            </div>

            <div class="modal-grid">
              <section class="modal-section">
                <h4>适用任务</h4>
                <div class="modal-chip-list">
                  <span v-for="task in skillTasks(detailSkill)" :key="task">{{ task }}</span>
                </div>
              </section>

              <section class="modal-section">
                <h4>知识输入</h4>
                <div class="modal-chip-list">
                  <span v-for="input in skillKnowledgeInputs(detailSkill)" :key="input">{{ input }}</span>
                </div>
              </section>

              <section class="modal-section">
                <h4>激活条件</h4>
                <p>{{ skillActivation(detailSkill) }}</p>
              </section>

              <section class="modal-section">
                <h4>来源与审查</h4>
                <p>{{ detailSkill.source }} · {{ skillSafety(detailSkill) }}</p>
              </section>

              <section class="modal-section">
                <h4>运行与文件</h4>
                <dl class="modal-fact-list">
                  <div>
                    <dt>状态</dt>
                    <dd>{{ statusLabel(detailSkill.status) }}</dd>
                  </div>
                  <div>
                    <dt>许可证</dt>
                    <dd>{{ detailSkill.license || '未标注' }}</dd>
                  </div>
                  <div>
                    <dt>运行时</dt>
                    <dd>{{ runtimeLabel(detailSkill.runtime) }}</dd>
                  </div>
                  <div>
                    <dt>本地 Skill</dt>
                    <dd>{{ detailSkill.localPath || '未下载' }}</dd>
                  </div>
                  <div>
                    <dt>上游路径</dt>
                    <dd>{{ detailSkill.skillPath || '未标注' }}</dd>
                  </div>
                  <div v-if="detailSkill.upstreamReferenceUrl">
                    <dt>参考来源</dt>
                    <dd>{{ detailSkill.upstreamReferenceUrl }}</dd>
                  </div>
                  <div v-if="detailSkill.sourceAvailableNotice">
                    <dt>许可说明</dt>
                    <dd>{{ detailSkill.sourceAvailableNotice }}</dd>
                  </div>
                </dl>
              </section>
            </div>

            <div class="modal-actions">
              <button
                class="modal-use-btn"
                :class="{ 'is-selected': isSkillSelected(detailSkill.id) }"
                type="button"
                @click="toggleSkill(detailSkill)"
              >
                <CheckCircle2 v-if="isSkillSelected(detailSkill.id)" />
                <Plus v-else />
                {{ isSkillSelected(detailSkill.id) ? '已加入使用列表' : '选择使用这个 Skill' }}
              </button>
            </div>
          </section>
        </div>
      </Transition>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Archive,
  BookOpen,
  BrainCircuit,
  BriefcaseBusiness,
  CheckCircle2,
  Database,
  DownloadCloud,
  ExternalLink,
  FileSearch,
  FileText,
  Globe2,
  Layers,
  LibraryBig,
  LockKeyhole,
  Network,
  Plus,
  PlugZap,
  RefreshCw,
  Scale,
  Search,
  Server,
  Shield,
  ShieldCheck,
  X,
} from 'lucide-vue-next'
import { safeExpertSkillDocumentPath, safeExternalHttpUrl } from '@/utils/externalUrl.js'

const CATALOG_URL = '/datasets/expert-skills/catalog.json'
const DB_STORAGE_KEY = 'globemind_academic_data_database_cards_v1'
const SELECTED_SKILLS_KEY = 'globemind_academic_data_selected_skills_v1'

const catalog = ref({
  version: '',
  updatedAt: '',
  policy: {},
  downloadedSources: [],
  domains: [],
})
const searchKeyword = ref('')
const activeDomainId = ref('all')
const databaseCards = ref([])
const selectedSkillIds = ref([])
const detailSkill = ref(null)
const dbFormError = ref('')
const catalogLoading = ref(true)
const catalogError = ref('')

const dbTypes = [
  { name: 'PostgreSQL', port: '5432' },
  { name: 'Milvus', port: '19530' },
  { name: 'ClickHouse', port: '8123' },
  { name: 'Elasticsearch', port: '9200' },
  { name: 'MySQL', port: '3306' },
  { name: 'MongoDB', port: '27017' },
  { name: 'Neo4j', port: '7687' },
  { name: 'Object Storage', port: '443' },
]

const dbForm = reactive({
  name: '',
  type: 'PostgreSQL',
  host: '',
  port: '5432',
  database: '',
  username: '',
  purpose: '',
  tags: '',
})

const domainIcons = {
  geo: Globe2,
  mil: Shield,
  econ: BriefcaseBusiness,
  law: Scale,
  academic: LibraryBig,
  psych: BrainCircuit,
  osint: FileSearch,
  research: FileSearch,
  mlops: Network,
  frontend: Layers,
  devops: Server,
  security: Shield,
  compliance: Scale,
  documents: FileText,
  data: Database,
  agent: BrainCircuit,
  engineering: Network,
  business: BriefcaseBusiness,
  product: Layers,
  marketing: Globe2,
  presentations: FileText,
  creative: LibraryBig,
  productivity: BrainCircuit,
  knowledge: BookOpen,
}

const domains = computed(() => catalog.value.domains || [])
const downloadedSources = computed(() => catalog.value.downloadedSources || [])
const catalogPolicy = computed(() => catalog.value.policy || {})
const catalogMeta = computed(() => {
  if (catalogLoading.value) return '正在加载目录'
  if (catalogError.value) return '目录不可用'
  return catalog.value.updatedAt ? `更新 ${catalog.value.updatedAt}` : '本地目录'
})

const totalSkills = computed(() =>
  domains.value.reduce((sum, domain) => sum + (domain.skills?.length || 0), 0),
)

const allSkills = computed(() =>
  domains.value.flatMap((domain) =>
    (domain.skills || []).map((skill) => ({
      ...skill,
      domainId: domain.id,
      domainName: domain.shortName || domain.name,
    })),
  ),
)

const activeDomain = computed(() =>
  domains.value.find((domain) => domain.id === activeDomainId.value) || null,
)

const activeDomainTitle = computed(() => {
  if (activeDomainId.value === 'all') return '全部专家 Skill'
  return activeDomain.value?.name || '专家 Skill'
})

const filteredSkills = computed(() => {
  const q = searchKeyword.value.trim().toLowerCase()
  return allSkills.value.filter((skill) => {
    if (activeDomainId.value !== 'all' && skill.domainId !== activeDomainId.value) return false
    if (!q) return true
    const haystack = [
      skill.name,
      skill.nameZh,
      skill.description,
      skill.descriptionZh,
      skill.domainName,
      skill.source,
      skill.status,
      skill.license,
      skill.runtime,
      skill.quality,
      skill.sourceRepo,
      skill.skillPath,
      skill.upstreamReferenceUrl,
      skill.sourceAvailableNotice,
      skill.activationHint,
      skill.activationHintZh,
      skill.safety,
      skill.safetyZh,
      ...(skill.tasks || []),
      ...(skill.tasksZh || []),
      ...(skill.knowledgeInputs || []),
      ...(skill.knowledgeInputsZh || []),
      ...(skill.requires || []),
      ...(skill.tags || []),
    ].join(' ').toLowerCase()
    return haystack.includes(q)
  })
})

const selectedSkillSet = computed(() => new Set(selectedSkillIds.value))

const selectedSkills = computed(() =>
  selectedSkillIds.value
    .map((id) => allSkills.value.find((skill) => skill.id === id))
    .filter(Boolean),
)

function domainIcon(id) {
  return domainIcons[id] || BookOpen
}

function trustLabel(level) {
  const labels = {
    official: '官方示例',
    standard: '开放标准',
    'community-curated': '社区索引',
    'community-registry': '社区注册表',
    'upstream-runtime': '上游运行时',
    'domain-specialist': '领域专家',
    'large-community-library': '大型社区库',
    'specialized-upstream': '专项上游',
    'internal-policy': '内部策略',
  }
  return labels[level] || level || '来源'
}

function statusLabel(status) {
  const labels = {
    available: '可用',
    'available-requires-runtime': '需运行时',
  }
  return labels[status] || status || '待审查'
}

function statusClass(status) {
  return status === 'available' ? 'ready' : 'runtime'
}

function runtimeLabel(runtime) {
  const labels = {
    'script-backed': '脚本支持',
    'tool-backed': '外部工具',
    'workflow-backed': '工作流',
    'reference-backed': '参考资料',
    'eval-backed': '评测流程',
  }
  return labels[runtime] || runtime || '未标注'
}

function skillName(skill) {
  return skill?.nameZh || skill?.name || '未命名 Skill'
}

function skillOriginalName(skill) {
  if (!skill?.name || !skill?.nameZh || skill.nameZh === skill.name) return ''
  return skill.name
}

function skillDescription(skill) {
  return skill?.descriptionZh || skill?.description || ''
}

function skillActivation(skill) {
  return skill?.activationHintZh || skill?.activationHint || '按任务需要启用。'
}

function skillSafety(skill) {
  return skill?.safetyZh || skill?.safety || '按需读取 Skill 原文并遵守最小权限。'
}

function skillTasks(skill) {
  return skill?.tasksZh?.length ? skill.tasksZh : (skill?.tasks || [])
}

function skillKnowledgeInputs(skill) {
  return skill?.knowledgeInputsZh?.length ? skill.knowledgeInputsZh : (skill?.knowledgeInputs || [])
}

function isSkillSelected(id) {
  return selectedSkillSet.value.has(id)
}

function toggleSkill(skill) {
  const id = skill?.id
  if (!id) return
  if (isSkillSelected(id)) {
    selectedSkillIds.value = selectedSkillIds.value.filter((skillId) => skillId !== id)
  } else {
    selectedSkillIds.value = [id, ...selectedSkillIds.value].slice(0, 12)
  }
  persistSelectedSkills()
}

function removeSelectedSkill(id) {
  selectedSkillIds.value = selectedSkillIds.value.filter((skillId) => skillId !== id)
  persistSelectedSkills()
}

function openSkillDetail(skill) {
  detailSkill.value = skill
}

function closeSkillDetail() {
  detailSkill.value = null
}

function persistSelectedSkills() {
  try {
    localStorage.setItem(SELECTED_SKILLS_KEY, JSON.stringify(selectedSkillIds.value))
  } catch (error) {
    console.warn('保存已使用 skill 失败', error)
  }
}

function loadSelectedSkills() {
  try {
    const raw = localStorage.getItem(SELECTED_SKILLS_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    selectedSkillIds.value = Array.isArray(parsed) ? parsed.filter(Boolean) : []
  } catch {
    selectedSkillIds.value = []
  }
}

function pruneSelectedSkills() {
  const validIds = new Set(allSkills.value.map((skill) => skill.id))
  if (!validIds.size || !selectedSkillIds.value.length) return
  const nextIds = selectedSkillIds.value.filter((id) => validIds.has(id))
  if (nextIds.length !== selectedSkillIds.value.length) {
    selectedSkillIds.value = nextIds
    persistSelectedSkills()
  }
}

function applyDefaultPort() {
  const item = dbTypes.find((type) => type.name === dbForm.type)
  if (item) dbForm.port = item.port
}

function normalizeTags(raw) {
  return String(raw || '')
    .split(/[,，\s]+/)
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 6)
}

function saveDatabaseCard() {
  dbFormError.value = ''
  if (!dbForm.name || !dbForm.host) {
    dbFormError.value = '请至少填写卡片名称和数据库 IP / Host。'
    return
  }
  const now = new Date().toISOString()
  const id =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : `db-${Date.now()}-${Math.random().toString(16).slice(2)}`
  const card = {
    id,
    name: dbForm.name,
    type: dbForm.type,
    host: dbForm.host,
    port: dbForm.port || '默认',
    database: dbForm.database,
    username: dbForm.username,
    purpose: dbForm.purpose,
    tags: normalizeTags(dbForm.tags),
    createdAt: now,
  }
  databaseCards.value = [card, ...databaseCards.value]
  persistDatabaseCards()
  resetDbForm()
}

function removeDatabaseCard(id) {
  databaseCards.value = databaseCards.value.filter((card) => card.id !== id)
  persistDatabaseCards()
}

function resetDbForm() {
  dbForm.name = ''
  dbForm.type = 'PostgreSQL'
  dbForm.host = ''
  dbForm.port = '5432'
  dbForm.database = ''
  dbForm.username = ''
  dbForm.purpose = ''
  dbForm.tags = ''
}

function persistDatabaseCards() {
  try {
    localStorage.setItem(DB_STORAGE_KEY, JSON.stringify(databaseCards.value))
  } catch (error) {
    console.warn('保存数据库卡片失败', error)
  }
}

function loadDatabaseCards() {
  try {
    const raw = localStorage.getItem(DB_STORAGE_KEY)
    databaseCards.value = raw ? JSON.parse(raw) : []
  } catch {
    databaseCards.value = []
  }
}

function openLocalSource(source) {
  const target = safeExpertSkillDocumentPath(source?.localPath)
  if (target) window.open(target, '_blank', 'noopener,noreferrer')
}

function openExternalSource(source) {
  const target = safeExternalHttpUrl(source?.repoUrl)
  if (target) window.open(target, '_blank', 'noopener,noreferrer')
}

async function loadCatalog() {
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const response = await fetch(CATALOG_URL)
    if (!response.ok) throw new Error(`catalog ${response.status}`)
    catalog.value = await response.json()
    pruneSelectedSkills()
  } catch (error) {
    console.error('加载专家 skill 目录失败:', error)
    catalogError.value = '请检查网络后重试。现有选择和数据库卡片不会受到影响。'
  } finally {
    catalogLoading.value = false
  }
}

function handleKeydown(event) {
  if (event.key === 'Escape' && detailSkill.value) closeSkillDetail()
}

onMounted(() => {
  loadDatabaseCards()
  loadSelectedSkills()
  loadCatalog()
  window.addEventListener('keydown', handleKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<style src="./AcademicData.css" scoped></style>
