<script setup>
import { computed, ref, onMounted } from 'vue'
import { getToken } from '@/utils/auth'
import { assistantApi } from '../api.js'
import { safeParseJson } from '../state.js'
import { isTextPreviewFile } from './kbUtils'

const CATALOG_URL = '/datasets/expert-skills/catalog.json'
const DB_STORAGE_KEY = 'globemind_academic_data_database_cards_v1'
const SELECTED_SKILLS_KEY = 'globemind_academic_data_selected_skills_v1'
const emit = defineEmits(['preview', 'context-changed'])

const kbCategories = ref([])
const kbActiveCategory = ref('geo')
const kbFiles = ref([])
const kbFilesLoading = ref(false)
const kbFilesCache = ref({})
const kbLoadSeq = ref(0)
const kbError = ref('')
const catalog = ref({ domains: [] })
const selectedSkillIds = ref([])
const databaseCards = ref([])

const allSkills = computed(() =>
  (catalog.value.domains || []).flatMap((domain) =>
    (domain.skills || []).map((skill) => ({
      ...skill,
      domainId: domain.id,
      domainName: domain.shortName || domain.name,
    })),
  ),
)

const selectedSkills = computed(() =>
  selectedSkillIds.value
    .map((id) => allSkills.value.find((skill) => skill.id === id))
    .filter(Boolean),
)

function skillName(skill) {
  return skill?.nameZh || skill?.name || '未命名 Skill'
}

async function fetchSkillCatalog() {
  try {
    catalog.value = await assistantApi.getSkillCatalog(CATALOG_URL)
    pruneSelectedSkillIds()
  } catch (e) {
    console.error('获取 skill 目录失败', e)
  }
}

function loadAcademicDataState() {
  selectedSkillIds.value = safeParseJson(localStorage.getItem(SELECTED_SKILLS_KEY), []).filter(Boolean)
  const cards = safeParseJson(localStorage.getItem(DB_STORAGE_KEY), [])
  databaseCards.value = Array.isArray(cards) ? cards : []
  pruneSelectedSkillIds()
}

function emitContextChanged() {
  emit('context-changed', {
    skills: selectedSkills.value,
    databaseCards: databaseCards.value,
  })
}

function persistSkillIds() {
  localStorage.setItem(SELECTED_SKILLS_KEY, JSON.stringify(selectedSkillIds.value))
}

function pruneSelectedSkillIds() {
  const validIds = new Set(allSkills.value.map((skill) => skill.id))
  if (!validIds.size || !selectedSkillIds.value.length) return
  const nextIds = selectedSkillIds.value.filter((id) => validIds.has(id))
  if (nextIds.length !== selectedSkillIds.value.length) {
    selectedSkillIds.value = nextIds
    persistSkillIds()
  }
}

function persistDatabaseCards() {
  localStorage.setItem(DB_STORAGE_KEY, JSON.stringify(databaseCards.value))
}

function removeSkill(skillId) {
  selectedSkillIds.value = selectedSkillIds.value.filter((id) => String(id) !== String(skillId))
  persistSkillIds()
  emitContextChanged()
}

function removeDatabaseCard(cardId) {
  databaseCards.value = databaseCards.value.filter((card) => String(card.id) !== String(cardId))
  persistDatabaseCards()
  emitContextChanged()
}

async function fetchKbCategories() {
  kbError.value = ''
  if (!getToken()) {
    kbCategories.value = []
    kbFiles.value = []
    kbError.value = '请先登录后查看知识库文件。'
    return
  }
  try {
    const json = await assistantApi.listKbCategories()
    if (!json.ok) throw new Error(json.detail || json.error || '获取知识库分类失败')
    const categories = Array.isArray(json.data) ? json.data : []
    kbCategories.value = categories
    const ids = new Set(categories.map((item) => String(item.id || '')))
    const first = categories[0]?.id || ''
    if (!ids.has(kbActiveCategory.value)) kbActiveCategory.value = first
  } catch (e) {
    kbError.value = e?.message || '获取知识库分类失败'
    kbCategories.value = []
    kbFiles.value = []
    console.error('获取知识库分类失败', e)
  }
}

async function fetchKbFiles(categoryId) {
  if (!getToken()) {
    kbError.value = '请先登录后查看知识库文件。'
    kbFiles.value = []
    return
  }
  const category = String(categoryId || '').trim()
  if (!category) return
  const seq = kbLoadSeq.value + 1
  kbLoadSeq.value = seq
  kbActiveCategory.value = category
  kbError.value = ''

  const cached = kbFilesCache.value[category]
  kbFiles.value = Array.isArray(cached) ? cached : []
  if (cached) {
    kbFilesLoading.value = false
    return
  }

  kbFilesLoading.value = true
  try {
    const json = await assistantApi.listKbFiles(category)
    if (seq !== kbLoadSeq.value) return
    if (!json.ok) throw new Error(json.detail || json.error || '获取知识库文件失败')
    const files = Array.isArray(json.data) ? json.data : []
    kbFilesCache.value = { ...kbFilesCache.value, [category]: files }
    kbFiles.value = files
  } catch (e) {
    if (seq === kbLoadSeq.value) {
      kbFiles.value = []
      kbError.value = e?.message || '获取知识库文件失败'
    }
    console.error('获取知识库文件失败', e)
  } finally {
    if (seq === kbLoadSeq.value) kbFilesLoading.value = false
  }
}

defineExpose({ fetchKbCategories })

onMounted(async () => {
  loadAcademicDataState()
  await Promise.all([fetchKbCategories(), fetchSkillCatalog()])
  emitContextChanged()
  if (!kbError.value && kbActiveCategory.value) fetchKbFiles(kbActiveCategory.value)
})
</script>

<template>
  <div class="ys-page">
    <div class="ys-page-head">
      <h1 class="ys-page-title">知识库</h1>
      <div class="ys-page-head-row">
        <p class="ys-page-sub">与 Academic Data 的已使用 Skill 和数据库卡片保持一致</p>
        <router-link class="kb-open-academic" to="/academic-data">打开智库信息汇聚</router-link>
      </div>
    </div>
    <section class="kb-agent-assets">
      <div class="kb-asset-panel">
        <div class="kb-asset-panel-head">
          <div>
            <p>Active Skills</p>
            <h2>已使用 Skill</h2>
          </div>
          <span>{{ selectedSkills.length }}</span>
        </div>
        <div v-if="selectedSkills.length" class="kb-skill-list">
          <article v-for="skill in selectedSkills" :key="skill.id" class="kb-skill-card">
            <div class="kb-skill-icon">{{ (skill.domainName || skillName(skill) || 'S').slice(0, 1) }}</div>
            <div>
              <strong>{{ skillName(skill) }}</strong>
              <small>{{ skill.domainName }}</small>
            </div>
            <button type="button" class="kb-card-remove" title="从 Hermes 上下文移除" @click="removeSkill(skill.id)">×</button>
          </article>
        </div>
        <div v-else class="kb-asset-empty">
          <span>还没有选择 Skill。</span>
          <router-link to="/academic-data">前往智库信息汇聚选择</router-link>
        </div>
      </div>

      <div class="kb-asset-panel">
        <div class="kb-asset-panel-head">
          <div>
            <p>Database Registry</p>
            <h2>数据库连接卡片</h2>
          </div>
          <span>{{ databaseCards.length }}</span>
        </div>
        <div v-if="databaseCards.length" class="kb-db-list">
          <article v-for="card in databaseCards" :key="card.id" class="kb-db-card">
            <div class="kb-db-card-head">
              <span>DB</span>
              <div>
                <strong>{{ card.name }}</strong>
                <small>{{ card.type }} · {{ card.host }}:{{ card.port }}</small>
              </div>
              <button type="button" class="kb-card-remove" title="从 Hermes 上下文移除" @click="removeDatabaseCard(card.id)">×</button>
            </div>
            <p>{{ card.purpose || '未填写用途说明。' }}</p>
            <div class="kb-db-tags">
              <span v-for="tag in card.tags || []" :key="tag">{{ tag }}</span>
            </div>
          </article>
        </div>
        <div v-else class="kb-asset-empty">
          <span>还没有数据库卡片。</span>
          <router-link to="/academic-data">前往智库信息汇聚保存连接说明</router-link>
        </div>
      </div>
    </section>

    <div v-if="kbError" class="kb-doc-empty kb-doc-empty--full">
      {{ kbError }}
    </div>
    <div v-else-if="!kbCategories.length" class="kb-doc-empty kb-doc-empty--full">
      暂无知识库分类。配置知识库目录后会在这里显示分类与文件。
    </div>
    <div v-else class="kb-layout">
      <div class="kb-cats">
        <button
          v-for="cat in kbCategories"
          :key="cat.id"
          class="kb-cat"
          :class="{ active: kbActiveCategory === cat.id, loading: kbFilesLoading && kbActiveCategory === cat.id }"
          @click="fetchKbFiles(cat.id)"
        >
          <span class="kb-cat-icon">{{ cat.icon }}</span>
          <span class="kb-cat-name">{{ cat.name }}</span>
          <span class="kb-cat-count">{{ cat.count }}</span>
        </button>
      </div>
      <div class="kb-docs">
        <div class="kb-docs-head">
          <span>{{ kbCategories.find(c => c.id === kbActiveCategory)?.name || '' }} · 文件列表</span>
          <span class="kb-doc-count">{{ kbFilesLoading ? '读取中' : `${kbFiles.length} 个文件` }}</span>
        </div>
        <div v-if="kbFilesLoading" class="kb-doc-skeleton-list" aria-label="正在读取文件">
          <div v-for="n in 5" :key="n" class="kb-doc-skeleton">
            <span></span>
            <div>
              <i></i>
              <b></b>
            </div>
          </div>
        </div>
        <div v-else-if="kbFiles.length === 0" class="kb-doc-empty">
          该分类下暂无文件，请将文件放入 <code>knowledge_base/{{ kbCategories.find(c => c.id === kbActiveCategory)?.id?.toUpperCase() || '' }}/</code> 目录
        </div>
        <template v-else>
          <div v-for="f in kbFiles" :key="f.name" class="kb-doc-card">
            <div class="kb-doc-icon">📄</div>
            <div class="kb-doc-info">
              <div class="kb-doc-title" :class="{ 'kb-doc-title--clickable': isTextPreviewFile(f.name) }" @click="isTextPreviewFile(f.name) && $emit('preview', f, kbActiveCategory)">{{ f.name }}</div>
              <div class="kb-doc-meta">{{ (f.size / 1024).toFixed(1) }} KB · {{ f.modified }}</div>
            </div>
            <button v-if="isTextPreviewFile(f.name)" class="kb-doc-action" @click="$emit('preview', f, kbActiveCategory)">查看</button>
            <span v-else class="kb-doc-action kb-doc-action--disabled">-</span>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ys-page { padding: 24px; background: #f6f8ff; min-height: 100%; }
.ys-page-head { border-bottom: 1px solid rgba(37, 99, 235, 0.12); padding-bottom: 16px; margin-bottom: 18px; }
.ys-page-title { margin: 0; font-size: 24px; font-weight: 950; letter-spacing: -0.02em; color: rgba(15, 23, 42, 0.94); }
.ys-page-head-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.ys-page-sub { margin: 8px 0 0; font-size: 13px; font-weight: 650; color: rgba(71, 85, 105, 0.86); line-height: 1.7; }
.ys-page-badge { font-size: 11px; font-weight: 700; color: rgba(59, 130, 246, 0.9); background: rgba(59, 130, 246, 0.08); padding: 4px 10px; border-radius: 999px; border: 1px solid rgba(59, 130, 246, 0.2); }
.kb-open-academic { height: 34px; display: inline-flex; align-items: center; justify-content: center; padding: 0 14px; border-radius: 10px; border: 1px solid rgba(37, 99, 235, 0.28); background: rgba(37, 99, 235, 0.08); color: #1d4ed8; font-size: 13px; font-weight: 850; text-decoration: none; white-space: nowrap; }
.kb-open-academic:hover { background: rgba(37, 99, 235, 0.14); }

.kb-agent-assets { display: grid; grid-template-columns: minmax(0, 0.92fr) minmax(0, 1.08fr); gap: 16px; margin-bottom: 18px; }
.kb-asset-panel { padding: 16px; border: 1px solid rgba(34, 95, 137, 0.14); border-radius: 16px; background: linear-gradient(180deg, #ffffff, #f8fbff); box-shadow: 0 12px 28px rgba(34, 88, 136, 0.08); min-width: 0; }
.kb-asset-panel-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.kb-asset-panel-head p { margin: 0 0 2px; color: rgba(37, 99, 235, 0.72); font-size: 11px; font-weight: 900; letter-spacing: 0.08em; text-transform: uppercase; }
.kb-asset-panel-head h2 { margin: 0; color: rgba(15, 23, 42, 0.94); font-size: 17px; font-weight: 950; }
.kb-asset-panel-head > span { min-width: 34px; height: 34px; display: inline-flex; align-items: center; justify-content: center; color: #fff; background: linear-gradient(145deg, #2563eb, #0891b2); border-radius: 999px; font-weight: 950; box-shadow: 0 10px 20px rgba(37, 99, 235, 0.18); }
.kb-skill-list, .kb-db-list { display: grid; gap: 10px; max-height: 280px; overflow: auto; padding-right: 2px; }
.kb-skill-card { display: grid; grid-template-columns: 38px minmax(0, 1fr) 28px; gap: 10px; align-items: center; padding: 10px; border: 1px solid rgba(34, 95, 137, 0.14); border-radius: 12px; background: #ffffff; transition: transform 0.16s ease, box-shadow 0.16s ease; }
.kb-skill-card:hover, .kb-db-card:hover { transform: translateY(-1px); box-shadow: 0 10px 22px rgba(34, 88, 136, 0.1); }
.kb-skill-icon { width: 38px; height: 38px; border-radius: 10px; display: grid; place-items: center; color: #2563eb; background: rgba(37, 99, 235, 0.08); font-weight: 950; }
.kb-skill-card strong, .kb-skill-card small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-skill-card strong { color: rgba(15, 23, 42, 0.92); font-size: 13px; font-weight: 900; }
.kb-skill-card small { margin-top: 2px; color: rgba(100, 116, 139, 0.88); font-size: 12px; font-weight: 700; }
.kb-db-card { padding: 12px; border: 1px solid rgba(34, 95, 137, 0.14); border-radius: 14px; background: #ffffff; transition: transform 0.16s ease, box-shadow 0.16s ease; }
.kb-db-card-head { display: grid; grid-template-columns: 42px minmax(0, 1fr) 28px; gap: 10px; align-items: center; }
.kb-db-card-head > span { width: 42px; height: 42px; display: grid; place-items: center; border-radius: 12px; color: #fff; background: #2563eb; font-weight: 950; font-size: 12px; }
.kb-db-card strong, .kb-db-card small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-db-card strong { font-size: 14px; font-weight: 950; color: rgba(15, 23, 42, 0.92); }
.kb-db-card small { margin-top: 2px; color: rgba(100, 116, 139, 0.88); font-size: 12px; font-weight: 760; }
.kb-db-card p { margin: 10px 0; color: rgba(71, 85, 105, 0.88); font-size: 12px; line-height: 1.55; font-weight: 680; }
.kb-card-remove { width: 28px; height: 28px; border: 0; border-radius: 999px; background: transparent; color: rgba(100, 116, 139, 0.72); cursor: pointer; font-size: 18px; line-height: 1; display: inline-flex; align-items: center; justify-content: center; }
.kb-card-remove:hover { background: rgba(239, 68, 68, 0.1); color: rgba(220, 38, 38, 0.9); }
.kb-db-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.kb-db-tags span { padding: 3px 8px; border-radius: 999px; color: #1d4ed8; background: rgba(37, 99, 235, 0.08); border: 1px solid rgba(37, 99, 235, 0.12); font-size: 11px; font-weight: 800; }
.kb-asset-empty { padding: 28px 14px; border-radius: 12px; background: rgba(248, 250, 252, 0.82); color: rgba(100, 116, 139, 0.9); font-size: 13px; line-height: 1.7; font-weight: 680; text-align: center; }
.kb-asset-empty span,
.kb-asset-empty a { display: block; }
.kb-asset-empty a { margin-top: 8px; color: #1d4ed8; font-weight: 850; text-decoration: none; }
.kb-asset-empty a:hover { text-decoration: underline; }

.kb-upload-btn { height: 36px; padding: 0 18px; border-radius: 10px; border: 1px solid rgba(37, 99, 235, 0.3); background: rgba(59, 130, 246, 0.08); color: rgba(37, 99, 235, 0.9); font-weight: 700; font-size: 13px; cursor: pointer; white-space: nowrap; }
.kb-upload-btn:hover { background: rgba(59, 130, 246, 0.14); }
.kb-layout { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 18px; min-height: 240px; }
.kb-cats { display: flex; flex-direction: column; gap: 6px; }
.kb-cat { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-radius: 12px; border: 1px solid rgba(148, 163, 184, 0.08); background: rgba(255,255,255,0.55); cursor: pointer; text-align: left; font: inherit; font-weight: 700; color: rgba(51, 65, 85, 0.88); transition: all 0.15s ease; }
.kb-cat:hover { background: #ffffff; border-color: rgba(37, 99, 235, 0.18); }
.kb-cat.active { background: rgba(37, 99, 235, 0.09); border-color: rgba(37, 99, 235, 0.22); color: #2563eb; }
.kb-cat.loading { border-color: rgba(37, 99, 235, 0.32); background: rgba(37, 99, 235, 0.12); }
.kb-cat-icon { font-size: 18px; }
.kb-cat-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-cat-count { font-size: 12px; font-weight: 600; color: rgba(100, 116, 139, 0.85); }
.kb-docs { border: 1px solid rgba(34, 95, 137, 0.12); border-radius: 16px; overflow: hidden; background: #ffffff; box-shadow: 0 12px 28px rgba(34, 88, 136, 0.07); }
.kb-docs-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-bottom: 1px solid rgba(15, 23, 42, 0.06); font-weight: 600; font-size: 13px; color: rgba(51, 65, 85, 0.9); }
.kb-doc-search { width: 100%; box-sizing: border-box; padding: 8px 12px; border: 1px solid rgba(15, 23, 42, 0.1); border-radius: 8px; font-size: 13px; outline: none; font-family: inherit; }
.kb-doc-search:focus { border-color: rgba(59, 130, 246, 0.4); box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.08); }
.kb-doc-card { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid rgba(15, 23, 42, 0.04); transition: background 0.15s ease; }
.kb-doc-card:hover { background: rgba(15, 23, 42, 0.02); }
.kb-doc-skeleton-list { padding: 12px 16px; display: grid; gap: 10px; }
.kb-doc-skeleton { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 12px; align-items: center; padding: 10px 0; border-bottom: 1px solid rgba(37, 99, 235, 0.06); }
.kb-doc-skeleton span,
.kb-doc-skeleton i,
.kb-doc-skeleton b { display: block; border-radius: 999px; background: linear-gradient(90deg, rgba(226,232,240,0.82), rgba(248,250,252,0.96), rgba(226,232,240,0.82)); background-size: 220% 100%; animation: kb-skeleton 1.1s ease-in-out infinite; }
.kb-doc-skeleton span { width: 30px; height: 30px; border-radius: 9px; }
.kb-doc-skeleton i { width: min(420px, 82%); height: 12px; margin-bottom: 8px; }
.kb-doc-skeleton b { width: 160px; height: 10px; }
@keyframes kb-skeleton {
  0% { background-position: 100% 0; }
  100% { background-position: -100% 0; }
}
.kb-doc-icon { font-size: 20px; flex-shrink: 0; }
.kb-doc-info { flex: 1; min-width: 0; }
.kb-doc-title { font-size: 14px; font-weight: 600; color: rgba(15, 23, 42, 0.88); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-doc-title--clickable { cursor: pointer; }
.kb-doc-title--clickable:hover { color: #2563eb; }
.kb-doc-meta { font-size: 12px; color: rgba(100, 116, 139, 0.85); margin-top: 2px; }
.kb-doc-action { flex-shrink: 0; height: 30px; padding: 0 14px; border-radius: 8px; border: 1px solid rgba(37, 99, 235, 0.25); background: rgba(59, 130, 246, 0.06); color: rgba(37, 99, 235, 0.85); font-weight: 600; font-size: 12px; cursor: pointer; }
.kb-doc-action:hover { background: rgba(59, 130, 246, 0.12); }
.kb-doc-action--disabled { opacity: 0.35; cursor: default; }
.kb-doc-count { font-size: 12px; color: rgba(100, 116, 139, 0.8); }
.kb-doc-empty { padding: 32px 16px; text-align: center; font-size: 13px; color: rgba(100, 116, 139, 0.85); line-height: 1.7; }
.kb-doc-empty--full { border: 1px solid rgba(34, 95, 137, 0.12); border-radius: 16px; background: #ffffff; box-shadow: 0 12px 28px rgba(34, 88, 136, 0.07); }
.kb-doc-empty code { font-weight: 600; background: rgba(15, 23, 42, 0.04); padding: 2px 6px; border-radius: 4px; }

@media (max-width: 1100px) {
  .kb-agent-assets, .kb-layout { grid-template-columns: 1fr; }
}
</style>
