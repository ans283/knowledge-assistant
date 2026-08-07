/**
 * One answer, in one of two states. Abstention is a first-class outcome with
 * its own affordance — not an error, and not an empty version of an answer.
 * It tells the reader what the corpus would have needed to contain.
 */
export default function AnswerCard({ response, activeCitation, onSelectCitation }) {
  const abstained = response.insufficient_context

  return (
    <div className={`answer ${abstained ? 'abstained' : 'grounded'}`}>
      <div className="answer-status">
        <span>{abstained ? 'No supporting passage' : 'Grounded in sources'}</span>
        <span className="spacer">
          confidence {response.confidence}
          {response.trace.citations_dropped > 0 &&
            ` · ${response.trace.citations_dropped} citation${
              response.trace.citations_dropped === 1 ? '' : 's'
            } discarded as unverifiable`}
        </span>
      </div>

      <div className="answer-body">
        <p>{response.answer}</p>
        {abstained && response.missing_information && (
          <div className="gap-note">
            <strong>What would be needed</strong>
            {response.missing_information}
          </div>
        )}
      </div>

      {response.citations.length > 0 && (
        <div className="sources">
          <div className="sources-label">
            {response.citations.length} verified passage
            {response.citations.length === 1 ? '' : 's'}
          </div>
          {response.citations.map((citation, index) => (
            <button
              key={`${citation.chunk_id}-${citation.char_start}`}
              className="pill"
              aria-current={citation === activeCitation}
              onClick={() => onSelectCitation(citation)}
            >
              <span className="pill-head">
                <span className="pill-index">[{index + 1}]</span>
                <span className="pill-doc">{citation.doc_title}</span>
                {citation.section_path && (
                  <span className="pill-section">{citation.section_path}</span>
                )}
              </span>
              <span className="pill-quote">“{citation.quote}”</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}