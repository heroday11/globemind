export function triggerBrowserDownload(
  blob,
  filename,
  { urlApi = globalThis.URL, documentRef = globalThis.document } = {},
) {
  const url = urlApi.createObjectURL(blob)
  const anchor = documentRef.createElement('a')
  anchor.href = url
  anchor.download = filename || 'download'
  documentRef.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  urlApi.revokeObjectURL(url)
}
