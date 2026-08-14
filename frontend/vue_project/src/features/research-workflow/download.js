const SAFE_FILENAME =
  /^research-reviewed-draft-[A-Za-z0-9_-]+-v[1-9][0-9]*-fields-[0-9a-f]{12}\.(?:json|md|html|csv)$/

export function triggerResearchArtifactDownload(
  artifact,
  { urlApi = globalThis.URL, documentRef = globalThis.document } = {},
) {
  if (
    !artifact?.blob ||
    !SAFE_FILENAME.test(String(artifact.filename || '')) ||
    typeof urlApi?.createObjectURL !== 'function' ||
    typeof urlApi?.revokeObjectURL !== 'function' ||
    typeof documentRef?.createElement !== 'function'
  ) {
    throw new TypeError('Research artifact download contract is invalid')
  }
  const url = urlApi.createObjectURL(artifact.blob)
  let anchor
  try {
    anchor = documentRef.createElement('a')
    anchor.href = url
    anchor.download = artifact.filename
    anchor.rel = 'noopener'
    documentRef.body.appendChild(anchor)
    anchor.click()
  } finally {
    anchor?.remove()
    urlApi.revokeObjectURL(url)
  }
}
