import { Bot, MessageCircle, UserRound } from 'lucide-react'
import { formatClock } from '../formatters'

export default function Conversation({ messages }) {
  return (
    <section className="panel conversation-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">CUSTOMER EXPERIENCE</p>
          <h2>Conversation</h2>
        </div>
        <MessageCircle size={18} className="heading-icon" />
      </div>
      {messages.length === 0 ? (
        <div className="empty-state conversation-empty">
          <MessageCircle size={22} />
          <p>No conversation messages yet.</p>
        </div>
      ) : (
        <div className="conversation-thread">
          {messages.map((message, index) => {
            const customer = message.direction === 'customer' || message.role === 'user'
            return (
              <div className={`message-line ${customer ? 'message-line-customer' : ''}`} key={`${message.created_at || message.ts || index}-${index}`}>
                <span className={`message-avatar ${customer ? 'avatar-customer' : 'avatar-mitra'}`}>
                  {customer ? <UserRound size={14} /> : <Bot size={14} />}
                </span>
                <div className={`message-bubble ${customer ? 'bubble-customer' : 'bubble-mitra'}`}>
                  <p>{message.text}</p>
                  <span>{customer ? 'Customer' : 'Mitra'} · {formatClock(message.created_at || message.ts)}</span>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
