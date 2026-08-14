/**
 * 统一 API 路径前缀（与 FastAPI 挂载的 /api 一致）。
 * 浏览器请求形如：${resolveApiOrigin()}${API_PREFIX}/dashboard/...
 */
export const API_PREFIX = '/api'

/**
 * Axios / 同源 fetch 的站点根：默认当前页 origin；若设置 VITE_API_BASE_URL 为绝对 URL，则取其 origin（不含 path）。
 * 禁止在业务代码中写死 localhost；开发代理目标由 .env.development 的 VITE_API_PROXY_TARGET 等配置。
 */
export function resolveApiOrigin() {
  if (typeof window === 'undefined') return ''
  const raw = import.meta.env.VITE_API_BASE_URL?.trim()
  if (!raw) return window.location.origin
  if (/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?(\/|$)/i.test(raw)) {
    console.warn('[api] VITE_API_BASE_URL 指向环回地址，已改用当前站点 origin（局域网请留空或设公网/内网网关 URL）')
    return window.location.origin
  }
  if (/^https?:\/\//i.test(raw)) {
    try {
      return new URL(raw).origin
    } catch {
      return window.location.origin
    }
  }
  return window.location.origin
}
