/**
 * Makes the retrieval legible rather than magical: what was considered, what
 * each scoring arm thought of it, what was sent to the model, and what it cost.
 *
 * Score directions differ and the header says so — cosine distance is
 * lower-is-better, everything else higher-is-better. A reader comparing raw
 * numbers without that would draw the wrong conclusion.
 */
const fmt = (value, digits = 3) =>
  value === null || value === undefined ? null : value.toFixed(digits)

export default function RetrievalInspector({ response, activeCitation, onSelectChunk }) {
  const { trace, retrieved, citations } = response
  const citedIds = new Set(citations.map((c) => c.chunk_id))

  return (
    <details className="inspector">
      <summary>
        Retrieval trace — {retrieved.length} candidates, {trace.chunks_sent_to_llm} sent to model
      </summary>
      <div className="inspector-body">
        <div className="trace">
          <span>mode <b>{trace.mode}</b></span>
          <span>considered <b>{trace.candidates_considered}</b></span>
          <span>retrieval <b>{Math.round(trace.retrieval_ms)}ms</b></span>
          {trace.rerank_ms > 0 && <span>rerank <b>{Math.round(trace.rerank_ms)}ms</b></span>}
          <span>generation <b>{Math.round(trace.generation_ms)}ms</b></span>
          <span>model <b>{trace.llm_model}</b></span>
          {trace.input_tokens != null && (
            <span>tokens <b>{trace.input_tokens} in / {trace.output_tokens} out</b></span>
          )}
          {trace.abstained_before_llm && <span><b>gated before generation</b></span>}
          {trace.injection_flagged > 0 && (
            <span><b>{trace.injection_flagged} chunk(s) flagged for directive language</b></span>
          )}
        </div>

        <table className="candidates">
          <thead>
            <tr>
              <th>Source</th>
              <th>Cosine ↓</th>
              <th>BM25 ↑</th>
              <th>RRF ↑</th>
              <th>Rerank ↑</th>
              <th>Passage</th>
            </tr>
          </thead>
          <tbody>
            {retrieved.map((chunk) => (
              <tr
                key={chunk.chunk_id}
                className={citedIds.has(chunk.chunk_id) ? 'cited' : undefined}
              >
                <td className="num">{chunk.meta.doc_id.slice(0, 26)}</td>
                <Score value={fmt(chunk.dense_distance)} />
                <Score value={fmt(chunk.sparse_score, 2)} />
                <Score value={fmt(chunk.fused_score, 4)} />
                <Score value={fmt(chunk.rerank_score, 2)} />
                <td className="text">{chunk.text.slice(0, 90)}…</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

/** An absent score means that arm did not retrieve this chunk — which is
 *  information, so it is rendered as a dash rather than a zero. */
function Score({ value }) {
  return value === null ? <td className="num absent">—</td> : <td className="num">{value}</td>
}