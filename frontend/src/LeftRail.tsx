import type { IndexStatus, KnowledgeNode, Repo } from "./api";
import KnowledgeTree from "./KnowledgeTree";

type NavItem = "home" | "repositories" | "chats" | "knowledge" | "settings";

type Props = {
  url: string;
  onUrlChange: (value: string) => void;
  busy: boolean;
  canIncremental: boolean;
  onAnalyze: () => void;
  onIncremental: () => void;
  status: IndexStatus | null;
  repo: Repo | null;
  error: string | null;
  completed: boolean;
  tree: KnowledgeNode[];
  collapsed: boolean;
  onToggleCollapse: () => void;
  onOpenGraph: () => void;
  onSelectFile: (path: string) => void;
  activeNav: NavItem;
  onNavChange: (nav: NavItem) => void;
  recentChats: { id: string; title: string; active?: boolean }[];
  onSelectChat: (id: string) => void;
};

const NAV_ITEMS: { id: NavItem; label: string; icon: string }[] = [
  { id: "home", label: "Home", icon: "⌂" },
  { id: "repositories", label: "Repositories", icon: "⎇" },
  { id: "chats", label: "AI Chats", icon: "💬" },
  { id: "knowledge", label: "Knowledge Base", icon: "📚" },
  { id: "settings", label: "Settings", icon: "⚙" },
];

export default function LeftRail({
  url,
  onUrlChange,
  busy,
  canIncremental,
  onAnalyze,
  onIncremental,
  status,
  repo,
  error,
  completed,
  tree,
  collapsed,
  onToggleCollapse,
  onOpenGraph,
  onSelectFile,
  activeNav,
  onNavChange,
  recentChats,
  onSelectChat,
}: Props) {
  if (collapsed) {
    return (
      <div className="left-rail-inner collapsed-inner">
        <button
          type="button"
          className="rail-icon-btn"
          onClick={onToggleCollapse}
          title="Expand sidebar"
        >
          »»
        </button>
        {NAV_ITEMS.slice(0, 4).map((item) => (
          <button
            key={item.id}
            type="button"
            className={`rail-icon-btn ${activeNav === item.id ? "active" : ""}`}
            onClick={() => {
              onNavChange(item.id);
              onToggleCollapse();
            }}
            title={item.label}
          >
            {item.icon}
          </button>
        ))}
      </div>
    );
  }

  const statusText = error
    ? error
    : status
      ? `${repo?.owner}/${repo?.name} — ${status.status} (${Math.round((status.progress || 0) * 100)}%)`
      : "Ready";

  const showRepoPanel = activeNav === "home" || activeNav === "repositories";
  const showKnowledge = activeNav === "knowledge" && completed;

  return (
    <div className="left-rail-inner">
      <div className="left-rail-header">
        <span className="rail-label">Navigation</span>
        <button
          type="button"
          className="rail-collapse-btn"
          onClick={onToggleCollapse}
          title="Collapse sidebar"
        >
          ««
        </button>
      </div>

      <nav className="side-nav">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`nav-item ${activeNav === item.id ? "active" : ""}`}
            onClick={() => onNavChange(item.id)}
          >
            <span className="nav-icon">{item.icon}</span>
            {item.label}
          </button>
        ))}
      </nav>

      <div className="recent-chats">
        <span className="rail-label">Recent Chats</span>
        <ul className="recent-chat-list">
          {recentChats.length === 0 ? (
            <li className="recent-chat-empty">No chats yet</li>
          ) : (
            recentChats.map((chat) => (
              <li key={chat.id}>
                <button
                  type="button"
                  className={`recent-chat-item ${chat.active ? "active" : ""}`}
                  onClick={() => onSelectChat(chat.id)}
                >
                  {chat.title}
                </button>
              </li>
            ))
          )}
        </ul>
      </div>

      {showRepoPanel && (
        <>
          <div className="rail-analyze">
            <input
              value={url}
              onChange={(e) => onUrlChange(e.target.value)}
              placeholder="https://github.com/owner/repo"
            />
            <div className="rail-analyze-actions">
              <button type="button" disabled={busy} onClick={onAnalyze}>
                Analyze
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy || !canIncremental}
                onClick={onIncremental}
                title={
                  !canIncremental
                    ? "Analyze this repo successfully first"
                    : "Update only changed files"
                }
              >
                Incremental
              </button>
            </div>
          </div>

          <div className={`rail-status ${error ? "error" : ""}`}>{statusText}</div>

          {completed && (
            <div className="rail-tools">
              <button type="button" className="rail-tool-btn" onClick={onOpenGraph}>
                Dependency Graph
              </button>
            </div>
          )}
        </>
      )}

      {showKnowledge && (
        <div className="rail-tree-section">
          <span className="rail-label">Knowledge Tree</span>
          <div className="rail-tree-scroll">
            <KnowledgeTree nodes={tree} onSelectFile={onSelectFile} />
          </div>
        </div>
      )}

      {activeNav === "settings" && (
        <div className="settings-panel">
          <p className="widget-muted">Settings coming soon.</p>
        </div>
      )}
    </div>
  );
}
