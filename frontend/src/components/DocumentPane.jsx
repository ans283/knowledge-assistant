import { useEffect, useRef, useState } from 'react'
import { fetchDocumentText } from '../api/client'

/**
 * Renders a source document with the cited span highlighted.
 *
 * This is the payoff for the offset discipline in the backend. The whole
 * mechanism is one string slice:
 *
 *     text.slice(0, char_start) + <mark>text.slice(char_start, char_end)</mark> + rest
 *
 * It works only because char_start/char_end are code-point indices into the
 * exact same canonical text this endpoint serves — the invariant asserted in
 * build_chunks and verified again in verify_citations. No client-side search,
 * no fuzzy matching, no re-derivation: the backend already did the work, and
 * JavaScript's string indices agree with Python's.
 */
export default function DocumentPane({ citations, activeCitation }) {
  const [document, setDocument] = useState(null)
  const [error, setError] = useState(null)
  const markRef = useRef(null)

  const docId = activeCitation?.doc_id ?? citations[0]?.doc_id ?? null

  useEffect(() => {
    if (!docId) {
      setDocument(null)
      return
    }
    let cancelled = false
    setError(null)
    fetchDocumentText(docId)
      .then((d) => { if (!cancelled) setDocument(d) })
      .catch((e) => { if (!cancelled) setError(e.message) })
    // Guard against a stale response landing after a newer request — the
    // classic async race when the user clicks two citations quickly.
    return () => { cancelled = true }
  }, [docId])

  useEffect(() => {
    markRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [activeCitation, document])

  if (error) {
    return (
      <section className="document">
        <div className="document-placeholder">
          Could not load this document: {error}. Re-run ingestion if the corpus changed.
        </div>
      </section>
    )
  }

  if (!document) {
    return (
      <section className="document">
        <div className="document-placeholder">
          Ask a question. Verified citations appear here in their source document,
          with the quoted passage marked.
        </div>
      </section>
    )
  }

  // Every citation in this document gets a quiet highlight; the selected one
  // gets the live marker. Sorting and disjointness matter: overlapping spans
  // would produce nested marks and broken offsets.
  const spans = citations
    .filter((c) => c.doc_id === document.doc_id)
    .map((c) => ({ ...c, isActive: c === activeCitation }))
    .sort((a, b) => a.char_start - b.char_start)

  const parts = []
  let cursor = 0
  spans.forEach((span, i) => {
    if (span.char_start < cursor) return // overlapping — skip rather than nest
    parts.push(document.text.slice(cursor, span.char_start))
    parts.push(
      <mark
        key={`${span.chunk_id}-${i}`}
        className={span.isActive ? 'span live' : 'span'}
        ref={span.isActive ? markRef : null}
      >
        {document.text.slice(span.char_start, span.char_end)}
      </mark>
    )
    cursor = span.char_end
  })
  parts.push(document.text.slice(cursor))

  return (
    <section className="document">
      <div className="document-head">
        <h2>{document.title}</h2>
        <div className="meta">
          {document.doc_id} · {document.char_count.toLocaleString()} characters
          {activeCitation && ` · showing ${activeCitation.char_start}–${activeCitation.char_end}`}
        </div>
      </div>
      <div className="document-body">{parts}</div>
    </section>
  )
}