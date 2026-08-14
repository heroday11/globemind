<template>
  <div class="md-renderer" v-html="renderedHtml"></div>
</template>

<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/core'
import 'highlight.js/styles/atom-one-dark.css'

/* ── 按需注册常用语言 ── */
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import shell from 'highlight.js/lib/languages/shell'

hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', shell)
hljs.registerLanguage('json', json)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('ts', typescript)

const props = defineProps({
  content: { type: String, default: '' },
})

let _md = null
function getMd() {
  if (_md) return _md
  _md = new MarkdownIt({
    html: false,
    linkify: true,
    typographer: true,
    breaks: true,
    highlight(str, lang) {
      let codeHtml = ''
      if (lang && hljs.getLanguage(lang)) {
        try {
          codeHtml = hljs.highlight(str, { language: lang, ignoreIllegals: true }).value
        } catch {
          codeHtml = escapeHtml(str)
        }
      } else {
        codeHtml = escapeHtml(str)
      }
      const langLabel = lang && hljs.getLanguage(lang) ? lang : ''
      return `<div class="md-code-block">
<div class="md-code-header">
  <span class="md-code-lang">${escapeHtml(langLabel)}</span>
  <button class="md-code-copy" data-code="${escapeHtml(str)}">复制</button>
</div>
<div class="md-code-body"><pre><code class="hljs${lang ? ' language-' + escapeHtml(lang) : ''}">${codeHtml}</code></pre></div>
</div>`
    },
  })
  return _md
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;')
}

const renderedHtml = computed(() => {
  if (!props.content) return ''
  return getMd().render(props.content)
})

/* ── 复制按钮功能（事件委托） ── */
import { onMounted, onUnmounted } from 'vue'

function handleCopyClick(e) {
  const btn = e.target.closest('.md-code-copy')
  if (!btn) return
  const code = btn.getAttribute('data-code') || ''
  if (!code) return
  navigator.clipboard.writeText(code).then(() => {
    const orig = btn.textContent
    btn.textContent = '已复制'
    btn.classList.add('copied')
    setTimeout(() => {
      btn.textContent = orig
      btn.classList.remove('copied')
    }, 2000)
  }).catch(() => {
    btn.textContent = '复制失败'
    setTimeout(() => { btn.textContent = '复制' }, 2000)
  })
}

onMounted(() => {
  document.addEventListener('click', handleCopyClick)
})
onUnmounted(() => {
  document.removeEventListener('click', handleCopyClick)
})
</script>

<style>
/* ── Markdown 渲染样式（全局，因 v-html 内容无 scoped 属性） ── */

.md-renderer {
  font-size: 14px;
  line-height: 1.75;
  color: #1e293b;
  word-break: break-word;
}

/* 标题层级 */
.md-renderer h1 {
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
  margin: 20px 0 12px;
  padding-bottom: 8px;
  border-bottom: 2px solid #e2e8f0;
}
.md-renderer h2 {
  font-size: 17px;
  font-weight: 700;
  color: #1e293b;
  margin: 18px 0 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid #eef2f6;
}
.md-renderer h3 {
  font-size: 15px;
  font-weight: 650;
  color: #334155;
  margin: 16px 0 8px;
}
.md-renderer h4, .md-renderer h5, .md-renderer h6 {
  font-size: 14px;
  font-weight: 650;
  color: #475569;
  margin: 12px 0 6px;
}

/* 段落 */
.md-renderer p {
  margin: 8px 0;
  line-height: 1.75;
}

/* 列表 */
.md-renderer ul, .md-renderer ol {
  margin: 6px 0;
  padding-left: 22px;
}
.md-renderer li {
  margin: 3px 0;
  line-height: 1.7;
}
.md-renderer li > ul, .md-renderer li > ol {
  margin: 2px 0;
}

/* 粗体 / 斜体 */
.md-renderer strong {
  font-weight: 700;
  color: #0f172a;
}
.md-renderer em {
  font-style: italic;
}

/* 行内代码 */
.md-renderer code:not(.hljs) {
  font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
  font-size: 13px;
  background: #f1f5f9;
  color: #be123c;
  padding: 1px 6px;
  border-radius: 4px;
}

/* 引用块 */
.md-renderer blockquote {
  margin: 10px 0;
  padding: 8px 14px;
  border-left: 4px solid #6366f1;
  background: #f8fafc;
  border-radius: 0 6px 6px 0;
  color: #475569;
}
.md-renderer blockquote p {
  margin: 4px 0;
}

/* 水平分割线 */
.md-renderer hr {
  border: none;
  border-top: 1px solid #e2e8f0;
  margin: 16px 0;
}

/* 表格 */
.md-renderer table {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 13px;
}
.md-renderer th, .md-renderer td {
  border: 1px solid #e2e8f0;
  padding: 8px 12px;
  text-align: left;
}
.md-renderer th {
  background: #f8fafc;
  font-weight: 650;
  color: #334155;
}
.md-renderer td {
  color: #475569;
}
.md-renderer tr:nth-child(even) td {
  background: #fafbfc;
}

/* 链接 */
.md-renderer a {
  color: #3b82f6;
  text-decoration: none;
}
.md-renderer a:hover {
  text-decoration: underline;
}

/* 图片 */
.md-renderer img {
  max-width: 100%;
  border-radius: 8px;
  margin: 8px 0;
}

/* ── 代码块（VS Code 风格） ── */
.md-code-block {
  margin: 12px 0;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #333;
  background: #1e1e1e;
}
.md-code-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #252526;
  border-bottom: 1px solid #181818;
  padding: 6px 14px;
  user-select: none;
}
.md-code-lang {
  font-size: 11px;
  font-weight: 600;
  color: #858585;
  text-transform: lowercase;
}
.md-code-copy {
  font-size: 11px;
  font-weight: 500;
  color: #a0a0a0;
  background: transparent;
  border: 1px solid #3c3c3c;
  border-radius: 4px;
  padding: 2px 10px;
  cursor: pointer;
  transition: all 0.15s;
}
.md-code-copy:hover {
  background: #3c3c3c;
  color: #d4d4d4;
}
.md-code-copy.copied {
  background: #1b5e20;
  border-color: #2e7d32;
  color: #a5d6a7;
}
.md-code-body {
  background: #1e1e1e;
  overflow-x: auto;
}
.md-code-body pre {
  margin: 0;
  padding: 14px 16px;
  line-height: 1.5;
  font-size: 13px;
  font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
  tab-size: 2;
}
.md-code-body pre code {
  font-family: inherit;
  background: none !important;
  padding: 0 !important;
}
</style>
