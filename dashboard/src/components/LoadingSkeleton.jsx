export default function LoadingSkeleton({ variant = 'case' }) {
  return (
    <section className={`panel loading-panel skeleton-${variant}`} aria-label="Loading">
      <span className="skeleton-line skeleton-short" />
      <span className="skeleton-line skeleton-title" />
      <span className="skeleton-line" />
      <span className="skeleton-line skeleton-medium" />
      {variant === 'feed' && Array.from({ length: 4 }, (_, index) => (
        <span className="skeleton-feed-row" key={index} />
      ))}
      {variant === 'kpis' && <div className="skeleton-kpi-grid">
        {Array.from({ length: 6 }, (_, index) => <span className="skeleton-kpi" key={index} />)}
      </div>}
      {variant === 'case' && <div className="skeleton-case-body">
        <span /><span /><span />
      </div>}
    </section>
  )
}
