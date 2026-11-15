# Frontend design rules for Paytm Mitra dashboard

Product: a live operations console for an AI support agent. Users are support leads.
Feel: calm, precise, trustworthy, like a fintech ops tool. Not a landing page.

## Tokens (define in tailwind.config / :root, use only these)
- Background #F6F8FB, surface #FFFFFF, border #E3E8EF, text #0B1B33, muted #5B6B82
- Accent (brand-inspired blue) #0A6CFF; navy for headings #002E6E
- Status: verified #0E9F6E, awaiting approval #D97706, blocked/escalated #DC2626
- Radius 8px for cards, 6px for buttons. One subtle shadow level, or borders only.
- Spacing scale: 4/8/12/16/24. Compact density.

## Typography
- UI: "Plus Jakarta Sans" or "IBM Plex Sans", with "Noto Sans Devanagari" fallback
- Mono: "IBM Plex Mono" for txn IDs, amounts, step payloads
- Use font-variant-numeric: tabular-nums for all money and counts

## Layout
- Three columns: transaction feed | case timeline | approval queue + metrics
- Sticky slim top bar with a live connection indicator (SSE status)
- Case timeline is a vertical list with a status dot per step, not big cards

## Components must have
- Loading skeleton, empty state, and error state
- Step status: pending / running / ok / blocked, each with an icon and color
- Approve and Reject buttons with clear confirm states

## Motion
- Only: new timeline step slides in (150ms), button press feedback. No parallax or bounce.

## Do not
- No gradients, glassmorphism, glow effects, or emoji icons (use lucide-react)
- No lorem ipsum. Use realistic data: ₹1,250, TXN1001, "Customer bank timeout"
- No hardcoded hex colors in components. Use tokens.
- No centered hero text or marketing copy