/**
 * 将 FastAPI / HTTP 错误体中的 detail 格式化为可读字符串。
 * detail 可能是 string、ValidationError 数组或对象。
 */
export function formatApiErrorDetail(data) {
  if (!data) return '请求失败'
  const d = data.detail
  if (d == null) return data.message || data.msg || '请求失败'
  if (typeof d === 'string') return d
  if (Array.isArray(d)) {
    return d
      .map((x) => {
        if (typeof x === 'string') return x
        if (x && typeof x.msg === 'string') return x.msg
        return JSON.stringify(x)
      })
      .filter(Boolean)
      .join('；')
  }
  if (typeof d === 'object' && typeof d.msg === 'string') return d.msg
  return '请求失败'
}
