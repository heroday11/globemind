/**
 * 浏览器仅在「安全上下文」中提供 crypto.randomUUID（HTTPS、localhost 等）。
 * 局域网用 http://192.168.x.x:5173 时不是安全上下文，randomUUID 会缺失，导致创建预警线等逻辑崩溃。
 */
export function randomId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    try {
      return crypto.randomUUID()
    } catch {
      /* ignore */
    }
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}
