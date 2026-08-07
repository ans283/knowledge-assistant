import { useEffect, useState } from 'react'
import { askQuestion, fetchHealth } from './api/client'
import AnswerCard from './components/AnswerCard'
import DocumentPane from './components/DocumentPane'
import RetrievalInspector from './components/RetrievalInspector'

const MODES = ['dense', 'sparse', 'hybrid', 'hybrid_rerank']

const EXAMPLES = [
  'How long does GitLab preserve user information after a law enforcement request?',
  'What does control PRV-06 require?',
  'What is the dental reimbursement cap for contractors?',
]

/**
 * Selection state lives here, not in AnswerCard, because two siblings need it:
 * the citation list highlights the selected pill, and the document pane scrolls
 * to the matching span. Lifting it to the nearest common ancestor keeps one
 * source of truth instead of two that can disagree.
 */
export default function App() {
  const [turns, setTurns] = useState([])
  const [active, setActive] = useState(null)
  const [mode, setMode] = useState('hybrid_rerank')
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [health, setHealth] = useState(null)

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => setHealth(null))
  }, [])

  async function ask(question) {
    const trimmed = question.trim()
    if (trimmed.length < 3 || pending) return

    setPending(true)
    setError(null)
    try {
      const response = await askQuestion({ question: trimmed, mode })
      setTurns((previous) => [...previous, response])
      setActive(response.citations[0] ?? null)
      setDraft('')
    } catch (e) {
      setError(e.message)
    } finally {
      setPending(false)
    }
  }

  const latest = turns[turns.length - 1]

  return (
    <div className="shell">
      <header className="masthead">
        <h1>Knowledge Assistant</h1>
        <span className="tagline">Answers cite their source, or say they can’t.</span>
        {health && (
          <span className="corpus-stat">
            {health.chunks_indexed} passages indexed · {health.llm_provider}
          </span>
        )}
      </header>

      <div className="split">
        <section className="conversation">
          <div className="thread">
            {turns.length === 0 ? (
              <div className="empty-state">
                <h2>Ask the corpus a question.</h2>
                <p>
                  Every claim is traced to a passage you can read in place. When the
                  documents don’t cover something, the answer says so and names the gap.
                </p>
                <ul>
                  {EXAMPLES.map((example) => (
                    <li key={example}>
                      <button onClick={() => ask(example)}>{example}</button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              turns.map((response, index) => (
                <div className="turn" key={index}>
                  <p className="question">{response.question}</p>
                  <AnswerCard
                    response={response}
                    activeCitation={active}
                    onSelectCitation={setActive}
                  />
                </div>
              ))
            )}
          </div>

          <form
            className="ask"
            onSubmit={(event) => {
              event.preventDefault()
              ask(draft)
            }}
          >
            <div className="ask-row">
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Ask about the indexed policies…"
                aria-label="Question"
              />
              <button type="submit" disabled={pending || draft.trim().length < 3}>
                {pending ? 'Searching…' : 'Ask'}
              </button>
            </div>
            <div className="modes">
              <span className="label">Retrieval</span>
              {MODES.map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={mode === option}
                  onClick={() => setMode(option)}
                >
                  {option}
                </button>
              ))}
            </div>
            {error && <div className="error">{error}</div>}
          </form>
        </section>

        <DocumentPane
          citations={latest?.citations ?? []}
          activeCitation={active}
        />
      </div>

      {latest && <RetrievalInspector response={latest} activeCitation={active} />}
    </div>
  )
}