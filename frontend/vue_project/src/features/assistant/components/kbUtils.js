/** Shared helpers for DataAssistant panels. */
const TEXT_EXTENSIONS = new Set([
  'txt', 'md', 'json', 'xml', 'html', 'csv', 'yaml', 'yml', 'toml',
  'js', 'ts', 'py', 'java', 'c', 'cpp', 'h', 'css', 'scss', 'sql',
  'sh', 'bat', 'env', 'cfg', 'ini', 'conf', 'log',
])

export function isTextPreviewFile(fname) {
  const ext = (fname || '').split('.').pop()?.toLowerCase() || ''
  return TEXT_EXTENSIONS.has(ext)
}
