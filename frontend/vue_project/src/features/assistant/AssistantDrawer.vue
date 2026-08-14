<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import DataAssistant from './AssistantExperience.vue'

const DRAWER_WIDTH_KEY = 'globemind_assistant_drawer_width_v1'
const FLOATING_POSITION_KEY = 'globemind_assistant_drawer_float_position_v1'
const DEFAULT_DRAWER_WIDTH = 420
const MIN_DRAWER_WIDTH = 320
const MAX_DRAWER_WIDTH = 720
const FLOATING_MAX_WIDTH = 520
const FLOATING_DEFAULT_HEIGHT = 640

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  drawerKey: {
    type: [Number, String],
    default: 0,
  },
  title: {
    type: String,
    default: '数据助手',
  },
  subtitle: {
    type: String,
    default: '基于当前页面上下文继续协作',
  },
  pageSkill: {
    type: Object,
    default: () => ({}),
  },
})

const emit = defineEmits(['update:modelValue', 'page-action', 'layout-change'])
const drawerWidth = ref(DEFAULT_DRAWER_WIDTH)
const drawerMode = ref('docked')
const isResizing = ref(false)
const isFloatingDragging = ref(false)
const floatingPosition = ref({ x: 0, y: 0 })
let resizeStartX = 0
let resizeStartWidth = DEFAULT_DRAWER_WIDTH
let floatingDragStart = { x: 0, y: 0, left: 0, top: 0 }
let floatingDragFromDocked = false
let previousBodyCursor = ''
let previousBodySelect = ''

function maxDrawerWidth() {
  if (typeof window === 'undefined') return MAX_DRAWER_WIDTH
  return Math.max(MIN_DRAWER_WIDTH, Math.min(MAX_DRAWER_WIDTH, window.innerWidth - 24))
}

function clampDrawerWidth(value) {
  const width = Number(value)
  if (!Number.isFinite(width)) return DEFAULT_DRAWER_WIDTH
  return Math.min(Math.max(width, MIN_DRAWER_WIDTH), maxDrawerWidth())
}

function readStoredDrawerWidth() {
  if (typeof localStorage === 'undefined') return DEFAULT_DRAWER_WIDTH
  const stored = Number(localStorage.getItem(DRAWER_WIDTH_KEY))
  return clampDrawerWidth(stored || DEFAULT_DRAWER_WIDTH)
}

function persistDrawerWidth() {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(DRAWER_WIDTH_KEY, String(Math.round(drawerWidth.value)))
}

function floatingPanelSize() {
  if (typeof window === 'undefined') return { width: DEFAULT_DRAWER_WIDTH, height: FLOATING_DEFAULT_HEIGHT }
  return {
    width: Math.min(Math.max(MIN_DRAWER_WIDTH, drawerWidth.value), FLOATING_MAX_WIDTH, window.innerWidth - 24),
    height: Math.min(FLOATING_DEFAULT_HEIGHT, window.innerHeight - 24),
  }
}

function defaultFloatingPosition() {
  if (typeof window === 'undefined') return { x: 24, y: 24 }
  const size = floatingPanelSize()
  return {
    x: Math.max(12, Math.round((window.innerWidth - size.width) / 2)),
    y: Math.max(12, Math.round((window.innerHeight - size.height) / 2)),
  }
}

function dockedFloatingStartPosition() {
  if (typeof window === 'undefined') return defaultFloatingPosition()
  const size = floatingPanelSize()
  return {
    x: Math.max(12, window.innerWidth - size.width - 12),
    y: 12,
  }
}

function clampFloatingPosition(pos) {
  if (typeof window === 'undefined') return pos
  const size = floatingPanelSize()
  const margin = 12
  return {
    x: Math.min(Math.max(margin, Number(pos.x) || margin), Math.max(margin, window.innerWidth - size.width - margin)),
    y: Math.min(Math.max(margin, Number(pos.y) || margin), Math.max(margin, window.innerHeight - size.height - margin)),
  }
}

function readStoredFloatingPosition() {
  if (typeof localStorage === 'undefined') return defaultFloatingPosition()
  try {
    const raw = localStorage.getItem(FLOATING_POSITION_KEY)
    if (!raw) return defaultFloatingPosition()
    return clampFloatingPosition(JSON.parse(raw))
  } catch {
    return defaultFloatingPosition()
  }
}

function persistFloatingPosition() {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(FLOATING_POSITION_KEY, JSON.stringify(floatingPosition.value))
}

const drawerPanelStyle = computed(() => ({
  width: `${drawerMode.value === 'floating' ? floatingPanelSize().width : drawerWidth.value}px`,
  ...(drawerMode.value === 'floating'
    ? {
        left: `${floatingPosition.value.x}px`,
        top: `${floatingPosition.value.y}px`,
        height: `${floatingPanelSize().height}px`,
      }
    : {}),
}))

function compactDrawerText(value, max = 72) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

const pageSkillSummary = computed(() => compactDrawerText(props.pageSkill?.displaySummary || props.pageSkill?.summary))
const pageSkillActions = computed(() => {
  const actions = props.pageSkill?.displayActions || props.pageSkill?.aiActions || props.pageSkill?.ai_actions || props.pageSkill?.actions || []
  return Array.isArray(actions)
    ? actions.map((item) => compactDrawerText(item, 18)).filter(Boolean).slice(0, 2)
    : []
})

function closeDrawer() {
  emit('update:modelValue', false)
  emitLayout({ open: false })
}

function forwardPageAction(action) {
  emit('page-action', action)
}

function handleWindowResize() {
  drawerWidth.value = clampDrawerWidth(drawerWidth.value)
  floatingPosition.value = clampFloatingPosition(floatingPosition.value)
  emitLayout()
}

function stopDrawerResize() {
  if (!isResizing.value) return
  isResizing.value = false
  persistDrawerWidth()
  window.removeEventListener('pointermove', handleDrawerResize)
  window.removeEventListener('pointerup', stopDrawerResize)
  document.body.style.cursor = previousBodyCursor
  document.body.style.userSelect = previousBodySelect
}

function handleDrawerResize(event) {
  if (!isResizing.value) return
  const nextWidth = resizeStartWidth + resizeStartX - event.clientX
  drawerWidth.value = clampDrawerWidth(nextWidth)
}

function startDrawerResize(event) {
  if (event.button !== 0) return
  event.preventDefault()
  resizeStartX = event.clientX
  resizeStartWidth = drawerWidth.value
  isResizing.value = true
  previousBodyCursor = document.body.style.cursor
  previousBodySelect = document.body.style.userSelect
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', handleDrawerResize)
  window.addEventListener('pointerup', stopDrawerResize)
}

function resetDrawerWidth() {
  drawerWidth.value = clampDrawerWidth(DEFAULT_DRAWER_WIDTH)
  persistDrawerWidth()
}

function emitLayout(overrides = {}) {
  emit('layout-change', {
    open: props.modelValue,
    mode: drawerMode.value,
    docked: props.modelValue && drawerMode.value === 'docked',
    width: drawerWidth.value,
    ...overrides,
  })
}

function dockDrawer() {
  drawerMode.value = 'docked'
  emitLayout()
}

function floatDrawer() {
  drawerMode.value = 'floating'
  floatingPosition.value = clampFloatingPosition(floatingPosition.value.x ? floatingPosition.value : defaultFloatingPosition())
  persistFloatingPosition()
  emitLayout()
}

function toggleDrawerMode() {
  if (drawerMode.value === 'floating') dockDrawer()
  else floatDrawer()
}

function stopFloatingDrag() {
  if (!isFloatingDragging.value) return
  isFloatingDragging.value = false
  floatingDragFromDocked = false
  persistFloatingPosition()
  window.removeEventListener('pointermove', handleFloatingDrag)
  window.removeEventListener('pointerup', stopFloatingDrag)
  document.body.style.cursor = previousBodyCursor
  document.body.style.userSelect = previousBodySelect
}

function handleFloatingDrag(event) {
  if (!isFloatingDragging.value) return
  const dx = event.clientX - floatingDragStart.x
  const dy = event.clientY - floatingDragStart.y
  if (floatingDragFromDocked && drawerMode.value !== 'floating') {
    if (Math.abs(dx) + Math.abs(dy) < 8) return
    drawerMode.value = 'floating'
    emitLayout()
  }
  floatingPosition.value = clampFloatingPosition({
    x: floatingDragStart.left + dx,
    y: floatingDragStart.top + dy,
  })
}

function startFloatingDrag(event) {
  if (event.button !== 0 || event.target?.closest?.('button')) return
  event.preventDefault()
  floatingDragFromDocked = drawerMode.value !== 'floating'
  if (floatingDragFromDocked) {
    floatingPosition.value = dockedFloatingStartPosition()
  }
  floatingDragStart = {
    x: event.clientX,
    y: event.clientY,
    left: floatingPosition.value.x,
    top: floatingPosition.value.y,
  }
  isFloatingDragging.value = true
  previousBodyCursor = document.body.style.cursor
  previousBodySelect = document.body.style.userSelect
  document.body.style.cursor = 'move'
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', handleFloatingDrag)
  window.addEventListener('pointerup', stopFloatingDrag)
}

onMounted(() => {
  drawerWidth.value = readStoredDrawerWidth()
  drawerMode.value = 'docked'
  floatingPosition.value = readStoredFloatingPosition()
  window.addEventListener('resize', handleWindowResize)
  emitLayout()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleWindowResize)
  if (isResizing.value) stopDrawerResize()
  if (isFloatingDragging.value) stopFloatingDrag()
})

watch(
  [() => props.modelValue, drawerWidth, drawerMode],
  () => emitLayout(),
)
</script>

<template>
  <Teleport to="body">
    <Transition name="gm-assistant-drawer">
      <div v-if="props.modelValue" class="gm-assistant-drawer" role="complementary" :aria-label="props.title">
        <aside
          class="gm-assistant-drawer__panel"
          :class="{
            'is-resizing': isResizing,
            'is-floating': drawerMode === 'floating',
            'is-docked': drawerMode === 'docked',
            'is-dragging': isFloatingDragging,
          }"
          :style="drawerPanelStyle"
        >
          <button
            v-if="drawerMode === 'docked'"
            type="button"
            class="gm-assistant-drawer__resize"
            aria-label="拖动调整助手宽度"
            title="拖动调整宽度，双击恢复默认"
            @pointerdown="startDrawerResize"
            @dblclick="resetDrawerWidth"
          ></button>
          <header
            class="gm-assistant-drawer__head"
            title="拖动可切换为浮窗"
            @pointerdown="startFloatingDrag"
          >
            <div>
              <span>GlobeMind</span>
              <strong>{{ props.title }}</strong>
              <small>{{ props.subtitle }}</small>
              <div v-if="pageSkillSummary || pageSkillActions.length" class="gm-assistant-drawer__skill">
                <em v-if="pageSkillSummary">{{ pageSkillSummary }}</em>
                <b v-for="action in pageSkillActions" :key="action">{{ action }}</b>
              </div>
            </div>
            <div class="gm-assistant-drawer__actions">
              <button
                type="button"
                class="gm-assistant-drawer__mode"
                :aria-label="drawerMode === 'floating' ? '停靠到右侧' : '切换为浮窗'"
                :title="drawerMode === 'floating' ? '停靠到右侧' : '切换为浮窗'"
                @click="toggleDrawerMode"
              >
                {{ drawerMode === 'floating' ? '停靠' : '浮窗' }}
              </button>
              <button type="button" class="gm-assistant-drawer__close" aria-label="关闭" @click="closeDrawer">×</button>
            </div>
          </header>
          <div class="gm-assistant-drawer__body">
            <DataAssistant
              :key="props.drawerKey"
              embedded
              :page-skill="props.pageSkill"
              @page-action="forwardPageAction"
            />
          </div>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.gm-assistant-drawer {
  position: fixed;
  inset: 0;
  z-index: 6000;
  pointer-events: none;
}

.gm-assistant-drawer__panel {
  position: absolute;
  width: clamp(360px, 30vw, 460px);
  max-width: calc(100vw - 24px);
  min-width: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
  border-left: 1px solid rgba(15, 23, 42, 0.12);
  background: #f6f8ff;
  box-shadow: -12px 0 28px rgba(15, 23, 42, 0.14);
  pointer-events: auto;
}

.gm-assistant-drawer__panel.is-docked {
  top: 0;
  right: 0;
  bottom: 0;
}

.gm-assistant-drawer__panel.is-floating {
  border: 1px solid rgba(15, 23, 42, 0.14);
  border-radius: 12px;
  box-shadow: 0 22px 60px rgba(15, 23, 42, 0.24);
}

.gm-assistant-drawer__panel.is-resizing {
  box-shadow: -14px 0 34px rgba(15, 23, 42, 0.18);
}

.gm-assistant-drawer__panel.is-floating.is-dragging {
  box-shadow: 0 26px 68px rgba(15, 23, 42, 0.28);
}

.gm-assistant-drawer__resize {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  z-index: 3;
  width: 10px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: col-resize;
}

.gm-assistant-drawer__resize::before {
  content: '';
  position: absolute;
  top: 12px;
  bottom: 12px;
  left: 3px;
  width: 2px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.42);
  opacity: 0;
  transition: opacity 0.16s ease;
}

.gm-assistant-drawer__resize:hover::before,
.gm-assistant-drawer__panel.is-resizing .gm-assistant-drawer__resize::before {
  opacity: 1;
}

.gm-assistant-drawer__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px 8px 14px;
  border-bottom: 1px solid rgba(15, 23, 42, 0.08);
  background: rgba(255, 255, 255, 0.82);
  cursor: grab;
}

.gm-assistant-drawer__panel.is-floating .gm-assistant-drawer__head {
  cursor: move;
}

.gm-assistant-drawer__panel.is-dragging .gm-assistant-drawer__head {
  cursor: grabbing;
}

.gm-assistant-drawer__head > div:first-child {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.gm-assistant-drawer__head span {
  color: #2563eb;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.gm-assistant-drawer__head strong {
  overflow: hidden;
  color: #0f172a;
  font-size: 15px;
  font-weight: 950;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gm-assistant-drawer__head small {
  overflow: hidden;
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gm-assistant-drawer__skill {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 6px;
  margin-top: 3px;
}

.gm-assistant-drawer__skill em {
  flex: 1 1 100%;
  overflow: hidden;
  color: #475569;
  font-size: 11px;
  font-style: normal;
  font-weight: 700;
  line-height: 1.35;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.gm-assistant-drawer__skill b {
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: #1d4ed8;
  font-size: 10px;
  font-weight: 850;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gm-assistant-drawer__actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}

.gm-assistant-drawer__mode,
.gm-assistant-drawer__close {
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  border: 1px solid rgba(148, 163, 184, 0.26);
  border-radius: 8px;
  background: #ffffff;
  color: #334155;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}

.gm-assistant-drawer__mode {
  width: auto;
  min-width: 42px;
  padding: 0 9px;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 900;
}

.gm-assistant-drawer__mode:hover {
  border-color: rgba(37, 99, 235, 0.32);
  background: rgba(239, 246, 255, 0.94);
}

.gm-assistant-drawer__close:hover {
  border-color: rgba(37, 99, 235, 0.3);
  color: #1d4ed8;
}

.gm-assistant-drawer__body {
  min-height: 0;
  overflow: hidden;
}

.gm-assistant-drawer-enter-active,
.gm-assistant-drawer-leave-active {
  transition: opacity 0.18s ease;
}

.gm-assistant-drawer-enter-active .gm-assistant-drawer__panel,
.gm-assistant-drawer-leave-active .gm-assistant-drawer__panel {
  transition: transform 0.22s ease;
}

.gm-assistant-drawer-enter-from,
.gm-assistant-drawer-leave-to {
  opacity: 0;
}

.gm-assistant-drawer-enter-from .gm-assistant-drawer__panel,
.gm-assistant-drawer-leave-to .gm-assistant-drawer__panel {
  transform: translateX(100%);
}

.gm-assistant-drawer-enter-from .gm-assistant-drawer__panel.is-floating,
.gm-assistant-drawer-leave-to .gm-assistant-drawer__panel.is-floating {
  transform: translateY(10px) scale(0.98);
}

@media (max-width: 640px) {
  .gm-assistant-drawer__panel {
    width: min(420px, 92vw);
    max-width: 100vw;
    min-width: 0;
  }
}

@media (min-width: 641px) and (max-width: 1180px) {
  .gm-assistant-drawer__panel {
    width: clamp(340px, 42vw, 430px);
    min-width: 0;
  }
}
</style>
