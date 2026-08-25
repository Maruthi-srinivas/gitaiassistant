type Props = {
  agentOnline: boolean;
  statusLabel: string;
  searchValue: string;
  onSearchChange: (value: string) => void;
  onSearchSubmit: () => void;
  userEmail?: string;
  onLogout?: () => void;
};

export default function TopHeader({
  agentOnline,
  statusLabel,
  searchValue,
  onSearchChange,
  onSearchSubmit,
  userEmail,
  onLogout,
}: Props) {
  return (
    <header className="top-header">
      <div className="header-left">
        <div className="header-brand">
          <svg className="github-mark" viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="currentColor"
              d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"
            />
          </svg>
          <span className="header-title">GitHub AI Assistant</span>
        </div>
        <span className="header-badge">LangChain · LangGraph</span>
      </div>

      <div className="header-search">
        <svg className="search-icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M20 20l-4-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
        <input
          value={searchValue}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onSearchSubmit()}
          placeholder="Search repositories, files or ask anything..."
        />
      </div>

      <div className="header-right">
        <div className={`agent-status ${agentOnline ? "online" : ""}`}>
          <span className="status-dot" />
          {agentOnline ? "Agent Online" : statusLabel}
        </div>
        <button type="button" className="header-icon-btn" title="Notifications" aria-label="Notifications">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              d="M18 8a6 6 0 10-12 0c0 7-3 7-3 7h18s-3 0-3-7M13.73 21a2 2 0 01-3.46 0"
            />
          </svg>
        </button>
        <button type="button" className="header-profile" aria-label="User menu" onClick={onLogout}>
          <span className="avatar">{(userEmail || "?").slice(0, 2).toUpperCase()}</span>
          <span className="profile-name">{userEmail || "Account"}</span>
          <span className="profile-logout">Sign out</span>
        </button>
      </div>
    </header>
  );
}
