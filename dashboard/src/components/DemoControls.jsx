import { useMemo, useState } from 'react'
import { MessageCircle, Send, Sparkles } from 'lucide-react'
import { useDashboardStore } from '../store'

const SCENARIOS = [
  ['failed_payment', 'Failed payment'],
  ['pending_payment', 'Pending settlement'],
  ['duplicate_small', 'Small duplicate'],
  ['duplicate_large', 'Large duplicate'],
  ['suspicious_refund', 'Suspicious refund'],
]

export default function DemoControls() {
  const [customerId, setCustomerId] = useState('web-1')
  const [language, setLanguage] = useState('en')
  const [draft, setDraft] = useState('')
  const runScenario = useDashboardStore((state) => state.runScenario)
  const sendChat = useDashboardStore((state) => state.sendChat)
  const allMessages = useDashboardStore((state) => state.chatMessages)
  const chatMessages = useMemo(
    () => allMessages.filter((message) => String(message.chat_id) === customerId),
    [allMessages, customerId],
  )

  const submitMessage = async (event) => {
    event.preventDefault()
    const text = draft.trim()
    if (!text) return
    setDraft('')
    await sendChat(customerId, text, language)
  }

  return (
    <section className="panel demo-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">SANDBOX</p>
          <h2>Demo controls</h2>
        </div>
        <Sparkles className="heading-icon" size={18} />
      </div>

      <label className="field-label" htmlFor="customer-id">Customer ID</label>
      <input
        id="customer-id"
        className="text-input"
        value={customerId}
        onChange={(event) => setCustomerId(event.target.value)}
        placeholder="web-1"
      />
      <label className="field-label language-label" htmlFor="language">Language</label>
      <select
        id="language"
        className="text-input"
        value={language}
        onChange={(event) => setLanguage(event.target.value)}
      >
        <option value="en">English</option>
        <option value="hi">Hindi</option>
      </select>

      <div className="scenario-grid">
        {SCENARIOS.map(([scenario, label]) => (
          <button
            type="button"
            className="scenario-button"
            key={scenario}
            onClick={() => runScenario(scenario, customerId, language)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="phone-chat">
        <div className="phone-chat-heading">
          <span className="phone-icon"><MessageCircle size={15} /></span>
          <div>
            <strong>Customer phone</strong>
            <span>{customerId || 'Set a customer ID'}</span>
          </div>
          <span className="phone-online" />
        </div>
        <div className="phone-messages" aria-live="polite">
          {chatMessages.length === 0 ? (
            <p className="phone-empty">Send a message to start a conversation.</p>
          ) : chatMessages.slice(-5).map((message, index) => (
            <div
              className={`phone-bubble ${message.direction === 'customer' ? 'phone-bubble-customer' : ''}`}
              key={`${message.created_at || message.ts || index}-${index}`}
            >
              {message.text}
            </div>
          ))}
        </div>
        <form className="phone-compose" onSubmit={submitMessage}>
          <input
            aria-label="Message to Mitra"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Describe a payment issue…"
          />
          <button type="submit" aria-label="Send message" disabled={!draft.trim()}>
            <Send size={15} />
          </button>
        </form>
      </div>
    </section>
  )
}
