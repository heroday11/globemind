/**
 * 清除可能残留在 document 上的内联样式，避免全屏子页影响主站渲染。
 * 把 body/html 设为深色后，切回主站仍带黑底；刷新因无内联覆盖而恢复正常。
 */
export function resetDocumentChromeInline() {
  if (typeof document === 'undefined') return
  for (const el of [document.documentElement, document.body]) {
    el.style.removeProperty('background')
    el.style.removeProperty('background-color')
    el.style.removeProperty('color-scheme')
  }
}
