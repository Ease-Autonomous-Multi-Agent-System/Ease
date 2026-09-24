/** The agent-graph mark picked in the design session: one planner node fanning out to agents. */
export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size * 0.75} viewBox="0 0 32 24" aria-hidden="true" focusable="false">
      <g stroke="var(--brand)" strokeWidth="2.2" strokeLinecap="round">
        <line x1="6" y1="13" x2="16" y2="6" />
        <line x1="16" y1="6" x2="26" y2="13" />
        <line x1="16" y1="6" x2="16" y2="19" />
      </g>
      <g fill="var(--brand)">
        <circle cx="6" cy="13" r="3.6" />
        <circle cx="16" cy="6" r="3.6" />
        <circle cx="26" cy="13" r="3.6" />
        <circle cx="16" cy="19" r="3.6" />
      </g>
    </svg>
  );
}
