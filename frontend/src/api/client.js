/**
 * Thin API layer. One place that knows about HTTP, so components never do.
 *
 * Document text is cached: a 44k-character policy does not change between
 * citations, and re-fetching it on every citation click would make the
 * highlight feel laggy.
 */
const BASE = '/api'

const docCache = new Map()

async function request(path, options) {
  const response = await fetch(`${BASE}${path}`, options)
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* response had no JSON body */
    }
    throw new Error(detail)
  }
  return response.json()
}

export function askQuestion({ question, mode = 'hybrid_rerank', topK = 5, docIds = null }) {
  return request('/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, mode, top_k: topK, doc_ids: docIds }),
  })
}

export async function fetchDocumentText(docId) {
  if (docCache.has(docId)) return docCache.get(docId)
  const document = await request(`/documents/${encodeURIComponent(docId)}/text`)
  docCache.set(docId, document)
  return document
}

export function fetchDocuments() {
  return request('/documents')
}

export function fetchHealth() {
  return request('/health')
}