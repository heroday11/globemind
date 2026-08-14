/** 当前是否使用 mock 模式 */
export function useMock(): boolean {
  const raw = (import.meta as any).env?.VITE_USE_FIN_API_MOCK
  if (raw === undefined || raw === null || raw === '') return false
  return String(raw).toLowerCase() === 'true'
}
