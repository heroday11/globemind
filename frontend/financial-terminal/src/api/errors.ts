/** Build a content-free transport error without reading an upstream body. */
export function safeFinancialApiError(response: Pick<Response, 'status'>): Error {
  const status = Number.isInteger(response.status) && response.status >= 100
    && response.status <= 599
    ? response.status
    : null
  const error = new Error(
    status === null
      ? 'Financial API request failed.'
      : `Financial API request failed (${status}).`,
  )
  error.name = 'FinancialApiError'
  return error
}
