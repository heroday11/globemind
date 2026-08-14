<script setup>
import { computed } from 'vue'
import { Check, RotateCcw, Type } from 'lucide-vue-next'
import {
  DEFAULT_DISPLAY_PREFERENCES,
  FONT_FAMILY_OPTIONS,
  FONT_SIZE_OFFSET_MAX,
  FONT_SIZE_OFFSET_MIN,
  LINE_HEIGHT_OPTIONS,
  useDisplayPreferences,
} from '@/composables/useDisplayPreferences.js'

const { preferences, updateDisplayPreferences, resetDisplayPreferences } = useDisplayPreferences()

const fontPreviewStacks = {
  system:
    'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  sans: '"Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
  serif: '"Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", SimSun, Georgia, serif',
}

const fontSizeLabel = computed(() => {
  const offset = preferences.fontSizeOffset
  if (offset === 0) return '标准'
  const labels = {
    '-2': '更小',
    '-1': '较小',
    1: '稍大',
    2: '较大',
    3: '最大',
  }
  return labels[offset]
})

const isDefault = computed(
  () =>
    preferences.fontFamily === DEFAULT_DISPLAY_PREFERENCES.fontFamily &&
    preferences.fontSizeOffset === DEFAULT_DISPLAY_PREFERENCES.fontSizeOffset &&
    preferences.lineHeight === DEFAULT_DISPLAY_PREFERENCES.lineHeight,
)

function updateFontSize(event) {
  updateDisplayPreferences({ fontSizeOffset: Number(event.target.value) })
}
</script>

<template>
  <div class="dp-panel" data-display-preferences>
    <header class="dp-heading">
      <div class="dp-heading-icon" aria-hidden="true">
        <Type :size="20" :stroke-width="1.9" />
      </div>
      <div>
        <span class="dp-kicker">DISPLAY &amp; TYPE</span>
        <h2>显示与字体</h2>
        <p>统一调整站内文字，修改后会立即应用到所有页面。</p>
      </div>
    </header>

    <section class="dp-section" aria-labelledby="dp-font-family-title">
      <div class="dp-section-head">
        <div>
          <h3 id="dp-font-family-title">字体方案</h3>
          <p>界面、长文和代码会分别使用匹配的字体类型。</p>
        </div>
        <span class="dp-value">{{
          FONT_FAMILY_OPTIONS.find((item) => item.value === preferences.fontFamily)?.label
        }}</span>
      </div>
      <div class="dp-option-grid dp-option-grid--fonts" role="group" aria-label="字体方案">
        <button
          v-for="option in FONT_FAMILY_OPTIONS"
          :key="option.value"
          type="button"
          class="dp-font-option"
          :class="{ 'is-selected': preferences.fontFamily === option.value }"
          :aria-pressed="preferences.fontFamily === option.value"
          @click="updateDisplayPreferences({ fontFamily: option.value })"
        >
          <span class="dp-check" aria-hidden="true">
            <Check v-if="preferences.fontFamily === option.value" :size="13" :stroke-width="2.4" />
          </span>
          <strong :style="{ fontFamily: fontPreviewStacks[option.value] }">{{
            option.sample
          }}</strong>
          <span class="dp-option-label">{{ option.label }}</span>
          <small>{{ option.description }}</small>
        </button>
      </div>
    </section>

    <section class="dp-section" aria-labelledby="dp-font-size-title">
      <div class="dp-section-head">
        <div>
          <h3 id="dp-font-size-title">全局字号</h3>
          <p>保留各页面原有层级，同时整体缩小或放大文字。</p>
        </div>
        <output class="dp-value" for="dp-font-size">{{ fontSizeLabel }}</output>
      </div>
      <div class="dp-range-wrap">
        <input
          id="dp-font-size"
          class="dp-range"
          type="range"
          :min="FONT_SIZE_OFFSET_MIN"
          :max="FONT_SIZE_OFFSET_MAX"
          step="1"
          :value="preferences.fontSizeOffset"
          aria-labelledby="dp-font-size-title"
          :aria-valuetext="fontSizeLabel"
          aria-describedby="dp-font-size-hint"
          @input="updateFontSize"
        />
        <div class="dp-range-labels" aria-hidden="true">
          <span>更紧凑</span>
          <span>标准</span>
          <span>更醒目</span>
        </div>
      </div>
      <p id="dp-font-size-hint" class="dp-note">
        提供六档字号，保留页面层级并限制在安全的桌面布局范围内。
      </p>
    </section>

    <section class="dp-section" aria-labelledby="dp-line-height-title">
      <div class="dp-section-head">
        <div>
          <h3 id="dp-line-height-title">文字行距</h3>
          <p>影响正文、列表、表格与助手回复的垂直阅读节奏。</p>
        </div>
      </div>
      <div class="dp-option-grid dp-option-grid--leading" role="group" aria-label="文字行距">
        <button
          v-for="option in LINE_HEIGHT_OPTIONS"
          :key="option.value"
          type="button"
          class="dp-leading-option"
          :class="{ 'is-selected': preferences.lineHeight === option.value }"
          :aria-pressed="preferences.lineHeight === option.value"
          @click="updateDisplayPreferences({ lineHeight: option.value })"
        >
          <span class="dp-leading-lines" :class="`is-${option.value}`" aria-hidden="true">
            <i></i><i></i><i></i>
          </span>
          <span>
            <strong>{{ option.label }}</strong>
            <small>{{ option.description }}</small>
          </span>
        </button>
      </div>
    </section>

    <section class="dp-preview" aria-label="显示效果预览">
      <div class="dp-preview-meta">
        <span>实时预览</span>
        <span>GLOBAL TYPOGRAPHY</span>
      </div>
      <h3>全球趋势研判</h3>
      <p>统一的字号层级和阅读节奏，让新闻线索、事件比较与报告内容在不同页面之间保持连贯。</p>
      <div class="dp-preview-tags" aria-hidden="true">
        <span>事件聚类</span>
        <span>风险信号</span>
        <span>知识引用</span>
      </div>
    </section>

    <footer class="dp-footer">
      <div>
        <strong>已自动保存</strong>
        <span>设置仅保存在此账号的当前浏览器中。</span>
      </div>
      <button type="button" class="dp-reset" :disabled="isDefault" @click="resetDisplayPreferences">
        <RotateCcw :size="15" :stroke-width="2" aria-hidden="true" />
        恢复默认
      </button>
    </footer>
  </div>
</template>

<style scoped>
.dp-panel {
  display: grid;
  gap: 18px;
  color: var(--pc-text, #1a1824);
}

.dp-heading {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding-bottom: 4px;
}

.dp-heading-icon {
  width: 42px;
  height: 42px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border: 1px solid rgba(91, 114, 223, 0.2);
  border-radius: 12px;
  color: var(--uc-accent, #5b72df);
  background: linear-gradient(145deg, #eef2ff, #ffffff);
  box-shadow: 0 6px 18px rgba(91, 114, 223, 0.1);
}

.dp-kicker {
  display: block;
  margin-bottom: 3px;
  color: var(--uc-accent, #5b72df);
  font-size: 0.66rem;
  font-weight: 760;
  letter-spacing: 0.14em;
}

.dp-heading h2,
.dp-section h3,
.dp-preview h3,
.dp-heading p,
.dp-section p,
.dp-preview p {
  margin: 0;
}

.dp-heading h2 {
  font-size: 1.14rem;
  line-height: 1.25;
  font-weight: 740;
}

.dp-heading p {
  margin-top: 4px;
  color: var(--pc-text-muted, #8b869e);
  font-size: 0.8rem;
  line-height: 1.55;
}

.dp-section {
  padding: 18px;
  border: 1px solid var(--pc-border, #e8e5f0);
  border-radius: 13px;
  background: #fdfcfe;
}

.dp-section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 14px;
}

.dp-section h3 {
  color: var(--pc-text, #1a1824);
  font-size: 0.92rem;
  line-height: 1.35;
  font-weight: 700;
}

.dp-section p {
  margin-top: 4px;
  color: var(--pc-text-muted, #8b869e);
  font-size: 0.75rem;
  line-height: 1.5;
}

.dp-value {
  flex: 0 0 auto;
  padding: 5px 10px;
  border: 1px solid #dfe5fb;
  border-radius: 999px;
  color: #4055bb;
  background: #f2f4ff;
  font-size: 0.72rem;
  font-weight: 700;
}

.dp-option-grid {
  display: grid;
  gap: 10px;
}

.dp-option-grid--fonts,
.dp-option-grid--leading {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.dp-font-option,
.dp-leading-option {
  position: relative;
  min-width: 0;
  border: 1px solid #e3e6ef;
  border-radius: 11px;
  color: var(--pc-text-secondary, #5c5870);
  background: #ffffff;
  cursor: pointer;
  text-align: left;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}

.dp-font-option:hover,
.dp-leading-option:hover {
  border-color: rgba(91, 114, 223, 0.38);
  transform: translateY(-1px);
}

.dp-font-option.is-selected,
.dp-leading-option.is-selected {
  border-color: rgba(91, 114, 223, 0.6);
  background: linear-gradient(150deg, rgba(238, 242, 255, 0.95), #ffffff 68%);
  box-shadow: 0 0 0 3px rgba(91, 114, 223, 0.08);
}

.dp-font-option {
  display: flex;
  flex-direction: column;
  min-height: 120px;
  padding: 15px 14px 13px;
}

.dp-check {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 19px;
  height: 19px;
  display: grid;
  place-items: center;
  border: 1px solid #d9ddea;
  border-radius: 50%;
  color: #ffffff;
  background: #ffffff;
}

.is-selected .dp-check {
  border-color: var(--uc-accent, #5b72df);
  background: var(--uc-accent, #5b72df);
}

.dp-font-option > strong {
  padding-right: 20px;
  color: #20263b;
  font-size: 1.08rem;
  line-height: 1.3;
  font-weight: 700;
}

.dp-option-label {
  margin-top: 13px;
  color: var(--pc-text, #1a1824);
  font-size: 0.8rem;
  font-weight: 680;
}

.dp-font-option small,
.dp-leading-option small {
  display: block;
  margin-top: 3px;
  color: var(--pc-text-muted, #8b869e);
  font-size: 0.68rem;
  line-height: 1.45;
}

.dp-range-wrap {
  padding: 4px 3px 0;
}

.dp-range {
  width: 100%;
  height: 24px;
  margin: 0;
  accent-color: var(--uc-accent, #5b72df);
  cursor: pointer;
}

.dp-range:focus-visible {
  outline: 3px solid rgba(91, 114, 223, 0.22);
  outline-offset: 3px;
  border-radius: 999px;
}

.dp-range-labels {
  display: flex;
  justify-content: space-between;
  margin-top: 2px;
  color: var(--pc-text-muted, #8b869e);
  font-size: 0.68rem;
  font-weight: 600;
}

.dp-section .dp-note {
  margin-top: 10px;
  color: #6b7280;
}

.dp-leading-option {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 72px;
  padding: 12px;
}

.dp-leading-lines {
  width: 30px;
  flex: 0 0 auto;
  display: grid;
  align-content: center;
}

.dp-leading-lines i {
  display: block;
  height: 2px;
  border-radius: 2px;
  background: #8290c8;
}

.dp-leading-lines.is-compact {
  gap: 4px;
}
.dp-leading-lines.is-standard {
  gap: 7px;
}
.dp-leading-lines.is-relaxed {
  gap: 10px;
}

.dp-leading-option strong {
  color: var(--pc-text, #1a1824);
  font-size: 0.78rem;
  font-weight: 690;
}

.dp-preview {
  position: relative;
  overflow: hidden;
  padding: 20px;
  border: 1px solid #dfe5fb;
  border-radius: 14px;
  background:
    radial-gradient(circle at 92% 10%, rgba(91, 114, 223, 0.14), transparent 34%),
    linear-gradient(145deg, #f6f8ff 0%, #ffffff 58%, #f4fbff 100%);
}

.dp-preview-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: #62709d;
  font-size: 0.66rem;
  line-height: 1.4;
  font-weight: 720;
  letter-spacing: 0.08em;
}

.dp-preview h3 {
  margin-top: 16px;
  color: #19233f;
  font-size: 1.22rem;
  line-height: 1.3;
  font-weight: 760;
}

.dp-preview p {
  max-width: 56ch;
  margin-top: 7px;
  color: #53617e;
  font-size: 0.86rem;
  line-height: 1.65;
}

.dp-preview-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 14px;
}

.dp-preview-tags span {
  padding: 5px 9px;
  border: 1px solid rgba(91, 114, 223, 0.16);
  border-radius: 999px;
  color: #5161a6;
  background: rgba(255, 255, 255, 0.74);
  font-size: 0.7rem;
  font-weight: 650;
}

.dp-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-top: 2px;
}

.dp-footer > div {
  display: grid;
  gap: 2px;
}

.dp-footer strong {
  color: #334155;
  font-size: 0.76rem;
  font-weight: 690;
}

.dp-footer span {
  color: var(--pc-text-muted, #8b869e);
  font-size: 0.7rem;
}

.dp-reset {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 8px 12px;
  border: 1px solid #dfe3ed;
  border-radius: 9px;
  color: #4b5563;
  background: #ffffff;
  cursor: pointer;
  font-size: 0.76rem;
  font-weight: 650;
}

.dp-reset:hover:not(:disabled) {
  border-color: rgba(91, 114, 223, 0.36);
  color: #4055bb;
  background: #f7f8ff;
}

.dp-reset:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

@media (max-width: 720px) {
  .dp-option-grid--fonts,
  .dp-option-grid--leading {
    grid-template-columns: 1fr;
  }

  .dp-font-option {
    min-height: 104px;
  }

  .dp-footer {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
