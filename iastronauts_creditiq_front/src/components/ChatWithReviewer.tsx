import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import 'highlight.js/styles/github-dark.css'
import { apiFetch } from '../lib/apiClient'

const API = import.meta.env.VITE_API_URL || ''

interface ChatTurn {
  turn: number
  user: string
  assistant: string
  timestamp: string
}

interface Props {
  jobId: string
  visible: boolean
  inline?: boolean
}

const SUGGESTIONS = [
  '¿Cuáles son los principales hallazgos del análisis?',
  '¿Qué cuentas presentan mayor materialidad?',
  '¿Cómo interpretar el score de riesgo de mercado?',
  '¿Qué notas NIIF se requieren?',
  '¿Qué variaciones son más relevantes para la junta?',
]

export default function ChatWithReviewer({ jobId, visible, inline = false }: Props) {
  const [open, setOpen] = useState(false)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Load history: inline mode loads on mount; floating mode loads on open
  useEffect(() => {
    const shouldLoad = inline ? visible : open
    if (!shouldLoad || turns.length > 0 || loadingHistory) return
    setLoadingHistory(true)
    apiFetch(`${API}/analyses/${jobId}/chat/history`)
      .then(r => r.json())
      .then(d => setTurns(d.turns || []))
      .catch(() => {})
      .finally(() => setLoadingHistory(false))
  }, [open, inline, visible, jobId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Scroll to bottom on new message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, loading])

  // Focus input when panel opens
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 150)
  }, [open])

  const send = async (text?: string) => {
    const message = (text ?? input).trim()
    if (!message || loading) return
    setInput('')
    setError(null)
    setLoading(true)

    // Optimistic user bubble
    const optimistic: ChatTurn = {
      turn: turns.length + 1,
      user: message,
      assistant: '',
      timestamp: new Date().toISOString(),
    }
    setTurns(prev => [...prev, optimistic])

    try {
      const res = await apiFetch(`${API}/analyses/${jobId}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, tenant_id: 'local' }),
      })
      const data = await res.json()
      if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`)
      setTurns(prev =>
        prev.map((t, i) =>
          i === prev.length - 1 ? { ...t, assistant: data.reply } : t
        )
      )
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Error de red')
      setTurns(prev => prev.slice(0, -1))
    } finally {
      setLoading(false)
    }
  }

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  if (!visible) return null

  // ── Shared sub-components ─────────────────────────────────────────────────

  const MessagesArea = (
    <div style={{
      flex: 1,
      overflowY: 'auto',
      padding: '16px 16px 8px',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}>
      {loadingHistory && (
        <div style={{ textAlign: 'center', color: '#4B5563', fontFamily: 'Inter', fontWeight: 300, fontSize: 12, padding: 20 }}>
          Cargando historial…
        </div>
      )}

      {!loadingHistory && turns.length === 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
          <div style={{ fontFamily: 'Inter', fontWeight: 300, fontSize: 12, color: '#64748B', textAlign: 'center', marginBottom: 6 }}>
            Haz una pregunta sobre el análisis
          </div>
          {SUGGESTIONS.map(s => (
            <button
              key={s}
              onClick={() => send(s)}
              style={{
                background: 'rgba(47, 128, 255, 0.04)',
                border: '1px solid rgba(47, 128, 255, 0.14)',
                borderRadius: 8,
                padding: '7px 12px',
                color: '#64748B',
                fontFamily: 'Inter',
                fontWeight: 300,
                fontSize: 12,
                textAlign: 'left',
                cursor: 'pointer',
                transition: 'border-color 0.15s, color 0.15s',
              }}
              onMouseEnter={e => {
                (e.target as HTMLButtonElement).style.borderColor = 'rgba(47,128,255,0.35)'
                ;(e.target as HTMLButtonElement).style.color = '#CBD5E1'
              }}
              onMouseLeave={e => {
                (e.target as HTMLButtonElement).style.borderColor = 'rgba(47,128,255,0.14)'
                ;(e.target as HTMLButtonElement).style.color = '#64748B'
              }}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {turns.map((t, i) => (
        <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <div style={{
              maxWidth: '82%',
              background: 'rgba(47, 128, 255, 0.08)',
              border: '1px solid rgba(47, 128, 255, 0.16)',
              borderRadius: '12px 12px 2px 12px',
              padding: '8px 13px',
              fontFamily: 'Inter',
              fontWeight: 300,
              fontSize: 13,
              color: '#CBD5E1',
              lineHeight: 1.6,
            }}>
              {t.user}
            </div>
          </div>

          {t.assistant ? (
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <div style={{
                width: 24, height: 24, borderRadius: '50%',
                background: 'rgba(86, 242, 193, 0.12)',
                border: '1px solid rgba(86, 242, 193, 0.25)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexShrink: 0, marginTop: 2,
              }}>
                <span className="material-symbols-outlined" style={{ fontSize: 13, color: '#56F2C1' }}>smart_toy</span>
              </div>
              <div style={{
                maxWidth: '88%',
                background: 'rgba(86, 242, 193, 0.04)',
                border: '1px solid rgba(86, 242, 193, 0.12)',
                borderRadius: '2px 12px 12px 12px',
                padding: '9px 13px',
              }} className="chat-md">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {t.assistant}
                </ReactMarkdown>
              </div>
            </div>
          ) : loading && i === turns.length - 1 ? (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', paddingLeft: 32 }}>
              <span style={{ fontFamily: 'Inter', fontWeight: 300, fontSize: 12, color: '#56F2C1' }}>Analizando</span>
              <Dots />
            </div>
          ) : null}
        </div>
      ))}

      {error && (
        <div style={{
          background: 'rgba(255, 77, 109, 0.08)',
          border: '1px solid rgba(255, 77, 109, 0.22)',
          borderRadius: 8, padding: '8px 12px',
          fontFamily: 'Inter', fontSize: 12, color: '#FF4D6D',
        }}>
          Error: {error}
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )

  const InputArea = (
    <div style={{
      padding: '10px 14px 14px',
      borderTop: '1px solid rgba(47, 128, 255, 0.12)',
      flexShrink: 0,
    }}>
      <div style={{
        display: 'flex', gap: 8, alignItems: 'flex-end',
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(47, 128, 255, 0.2)',
        borderRadius: 10, padding: '8px 10px',
      }}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          rows={1}
          placeholder="Pregunta sobre el análisis… (Enter para enviar)"
          disabled={loading}
          style={{
            flex: 1, background: 'none', border: 'none', outline: 'none',
            resize: 'none', fontFamily: 'Inter', fontWeight: 300, fontSize: 13,
            color: '#E2E8F0', lineHeight: 1.6, maxHeight: 120, overflowY: 'auto',
          }}
        />
        <button
          onClick={() => send()}
          disabled={loading || !input.trim()}
          style={{
            background: input.trim() && !loading ? 'rgba(86, 242, 193, 0.15)' : 'transparent',
            border: 'none', borderRadius: 6,
            cursor: input.trim() && !loading ? 'pointer' : 'default',
            color: input.trim() && !loading ? '#56F2C1' : '#374151',
            padding: '4px 6px', display: 'flex',
            transition: 'color 0.15s, background 0.15s', flexShrink: 0,
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 20 }}>send</span>
        </button>
      </div>
      <div style={{
        fontFamily: 'JetBrains Mono', fontSize: 9, color: '#374151',
        textAlign: 'center', marginTop: 6,
      }}>
        SHIFT+ENTER para nueva línea · las respuestas se guardan en S3
      </div>
    </div>
  )

  // ── Inline mode: render as a card inside the Agent 5 view ─────────────────
  if (inline) {
    return (
      <div className="bg-surface border border-border rounded-lg overflow-hidden flex flex-col" style={{ minHeight: 560, maxHeight: 820 }}>
        {/* Header */}
        <div style={{
          padding: '12px 16px',
          borderBottom: '1px solid rgba(47, 128, 255, 0.14)',
          display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0,
          background: 'rgba(86, 242, 193, 0.03)',
        }}>
          <span className="material-symbols-outlined" style={{ fontSize: 18, color: '#56F2C1' }}>smart_toy</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: 'Inter', fontSize: 13, fontWeight: 600, color: '#F5F7FA' }}>
              Chat con el Revisor Inteligente
            </div>
            <div style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#56F2C1', marginTop: 1 }}>
              Agente 5 · contexto completo del análisis
            </div>
          </div>
          {turns.length > 0 && (
            <span style={{
              background: 'rgba(86,242,193,0.15)', color: '#56F2C1',
              border: '1px solid rgba(86,242,193,0.3)',
              borderRadius: 999, padding: '1px 8px', fontSize: 10, fontWeight: 700,
              fontFamily: 'JetBrains Mono',
            }}>
              {turns.length} msg
            </span>
          )}
        </div>
        {MessagesArea}
        {InputArea}
      </div>
    )
  }

  // ── Floating mode: trigger button + slide-in panel ────────────────────────
  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          title="Chatear con el Revisor IA"
          style={{
            position: 'fixed', bottom: 28, right: 28, zIndex: 50,
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '10px 18px', borderRadius: 999,
            border: '1px solid rgba(86, 242, 193, 0.35)',
            background: 'linear-gradient(135deg, #0D1B2A 0%, #112240 100%)',
            color: '#56F2C1', fontFamily: 'JetBrains Mono', fontSize: 12,
            fontWeight: 600, cursor: 'pointer',
            boxShadow: '0 4px 24px rgba(86, 242, 193, 0.18)',
            transition: 'box-shadow 0.2s',
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>chat</span>
          Revisor IA
          {turns.length > 0 && (
            <span style={{
              background: '#56F2C1', color: '#0A0E1A',
              borderRadius: 999, padding: '1px 6px', fontSize: 10, fontWeight: 700,
            }}>
              {turns.length}
            </span>
          )}
        </button>
      )}

      {open && (
        <div style={{
          position: 'fixed', top: 0, right: 0, bottom: 0, width: 420,
          zIndex: 60, display: 'flex', flexDirection: 'column',
          background: '#0B1023',
          borderLeft: '1px solid rgba(47, 128, 255, 0.18)',
          boxShadow: '-8px 0 40px rgba(0,0,0,0.5)',
        }}>
          {/* Header */}
          <div style={{
            padding: '14px 18px',
            borderBottom: '1px solid rgba(47, 128, 255, 0.14)',
            display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0,
          }}>
            <span className="material-symbols-outlined" style={{ fontSize: 20, color: '#56F2C1' }}>smart_toy</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontFamily: 'Inter', fontSize: 14, fontWeight: 600, color: '#F5F7FA' }}>
                Revisor Inteligente
              </div>
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#56F2C1', marginTop: 1 }}>
                Agente 5 · {jobId.slice(0, 16)}…
              </div>
            </div>
            <button
              onClick={() => setOpen(false)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#64748B', padding: 4, display: 'flex' }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>close</span>
            </button>
          </div>
          {MessagesArea}
          {InputArea}
        </div>
      )}
    </>
  )
}

function Dots() {
  return (
    <span style={{ display: 'inline-flex', gap: 3 }}>
      {[0, 1, 2].map(i => (
        <span
          key={i}
          style={{
            width: 5,
            height: 5,
            borderRadius: '50%',
            background: '#56F2C1',
            opacity: 0.7,
            animation: `dot-pulse 1.2s ease-in-out ${i * 0.2}s infinite`,
          }}
        />
      ))}
      <style>{`
        @keyframes dot-pulse {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.3; }
          40% { transform: scale(1); opacity: 0.9; }
        }

        /* ── Markdown prose inside chat bubbles ── */
        .chat-md { font-family: Inter, sans-serif; font-weight: 300; font-size: 13px; color: #CBD5E1; line-height: 1.75; }
        .chat-md p { margin: 0 0 0.65em; }
        .chat-md p:last-child { margin-bottom: 0; }
        .chat-md h1, .chat-md h2, .chat-md h3, .chat-md h4 {
          font-family: Inter, sans-serif; font-weight: 500; color: #E2E8F0;
          margin: 1em 0 0.35em; letter-spacing: -0.01em;
        }
        .chat-md h1 { font-size: 15px; }
        .chat-md h2 { font-size: 13.5px; }
        .chat-md h3 { font-size: 13px; }
        .chat-md h4 { font-size: 12px; color: #94A3B8; font-weight: 400; }
        .chat-md ul, .chat-md ol { padding-left: 1.25em; margin: 0.35em 0 0.65em; }
        .chat-md li { margin-bottom: 0.3em; }
        .chat-md li > p { margin-bottom: 0.2em; }
        .chat-md strong { font-weight: 500; color: #E2E8F0; }
        .chat-md em { color: #94A3B8; font-style: italic; font-weight: 300; }
        .chat-md code:not(pre code) {
          font-family: 'JetBrains Mono', monospace; font-size: 11.5px;
          background: rgba(47, 128, 255, 0.09);
          border: 1px solid rgba(47, 128, 255, 0.18);
          border-radius: 4px; padding: 1px 5px; color: #7DD3FC; font-weight: 400;
        }
        .chat-md pre {
          background: rgba(3, 4, 14, 0.7); border: 1px solid rgba(47, 128, 255, 0.14);
          border-radius: 8px; padding: 11px 14px; overflow-x: auto;
          margin: 0.5em 0 0.75em; font-size: 12px; line-height: 1.55;
        }
        .chat-md pre code { background: none; border: none; padding: 0; color: inherit; font-size: inherit; font-weight: 400; }
        .chat-md blockquote {
          border-left: 2px solid rgba(86, 242, 193, 0.35);
          margin: 0.5em 0; padding: 3px 12px;
          color: #64748B; font-style: normal; font-weight: 300;
        }
        .chat-md table { width: 100%; border-collapse: collapse; font-size: 12px; margin: 0.6em 0; font-weight: 300; }
        .chat-md th {
          text-align: left; padding: 5px 10px;
          background: rgba(47, 128, 255, 0.06);
          border-bottom: 1px solid rgba(47, 128, 255, 0.18);
          font-family: 'JetBrains Mono', monospace; font-size: 10px; font-weight: 400;
          color: #64748B; text-transform: uppercase; letter-spacing: 0.06em;
        }
        .chat-md td { padding: 5px 10px; border-bottom: 1px solid rgba(47, 128, 255, 0.07); vertical-align: top; color: #CBD5E1; }
        .chat-md tr:last-child td { border-bottom: none; }
        .chat-md hr { border: none; border-top: 1px solid rgba(47,128,255,0.1); margin: 0.75em 0; }
        .chat-md a { color: #60A5FA; text-decoration: underline; text-underline-offset: 2px; font-weight: 300; }
      `}</style>
    </span>
  )
}
