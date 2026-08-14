export const DIALOG_FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

export function getDialogFocusableElements(container) {
  if (!container || typeof container.querySelectorAll !== 'function') return []
  return [...container.querySelectorAll(DIALOG_FOCUSABLE_SELECTOR)].filter((element) => (
    typeof element?.focus === 'function'
    && element.hidden !== true
    && element.getAttribute?.('aria-hidden') !== 'true'
    && element.getAttribute?.('aria-disabled') !== 'true'
  ))
}

export function trapDialogTab(event, container, activeElement = globalThis.document?.activeElement) {
  if (event?.key !== 'Tab') return false

  const focusable = getDialogFocusableElements(container)
  if (focusable.length === 0) {
    event.preventDefault?.()
    container?.focus?.()
    return true
  }

  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  const activeIsInside = Boolean(container?.contains?.(activeElement))
  let nextTarget = null

  if (!activeIsInside) nextTarget = event.shiftKey ? last : first
  else if (event.shiftKey && activeElement === first) nextTarget = last
  else if (!event.shiftKey && activeElement === last) nextTarget = first

  if (!nextTarget) return false
  event.preventDefault?.()
  nextTarget.focus()
  return true
}

export function restoreDialogFocus(target) {
  if (!target || target.isConnected === false || typeof target.focus !== 'function') return false
  try {
    target.focus({ preventScroll: true })
  } catch {
    target.focus()
  }
  return true
}
