import type { IndexStatus, Repo } from "./api";

type Props = {
  repo: Repo | null;
  status: IndexStatus | null;
  completed: boolean;
  indexing: boolean;
  chatBusy: boolean;
  fileCount: number;
  sourceCount: number;
  recentActivity: { label: string; time: string; ok: boolean }[];
};

const TOOLS = [
  { name: "GitHub API", online: true },
  { name: "Retriever", online: true },
  { name: "LLM (GPT-4 / Claude)", online: true },
  { name: "File Tools", online: true },
];

const STAT_BARS = [
  { label: "Code Quality", value: 92 },
  { label: "Documentation", value: 76 },
  { label: "Test Coverage", value: 58 },
  { label: "Maintainability", value: 88 },
];

export default function RightSidebar({
  repo,
  status,
  completed,
  indexing,
  chatBusy,
  fileCount,
  sourceCount,
  recentActivity,
}: Props) {
  const progress = status ? Math.round((status.progress || 0) * 100) : 0;

  const steps = [
    { label: "Searching repository", done: completed || indexing, active: indexing },
    { label: "Indexing files", done: completed, active: indexing && progress > 20 },
    { label: "Building knowledge graph", done: completed, active: indexing && progress > 50 },
    { label: "Generating response", done: false, active: chatBusy },
  ];

  return (
    <aside className="right-sidebar">
      <section className="widget-card">
        <h3 className="widget-title">Agent Workflow</h3>
        <ol className="workflow-steps">
          {steps.map((step, i) => (
            <li
              key={step.label}
              className={`workflow-step ${step.done ? "done" : ""} ${step.active ? "active" : ""}`}
            >
              <span className="step-marker">
                {step.done ? (
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path fill="none" stroke="currentColor" strokeWidth="2.5" d="M5 13l4 4L19 7" />
                  </svg>
                ) : (
                  <span className="step-num">{i + 1}</span>
                )}
              </span>
              <span className="step-label">{step.label}</span>
            </li>
          ))}
        </ol>
      </section>

      <section className="widget-card">
        <h3 className="widget-title">Tools</h3>
        <ul className="tools-list">
          {TOOLS.map((tool) => (
            <li key={tool.name}>
              <span className={`tool-dot ${tool.online && completed ? "online" : ""}`} />
              {tool.name}
            </li>
          ))}
        </ul>
      </section>

      <section className="widget-card">
        <h3 className="widget-title">Repository Stats</h3>
        {repo && completed ? (
          <>
            <div className="repo-mini-stats">
              <span>{fileCount} files indexed</span>
              {sourceCount > 0 && <span>{sourceCount} sources cited</span>}
            </div>
            <div className="stat-bars">
              {STAT_BARS.map((bar) => (
                <div key={bar.label} className="stat-bar-row">
                  <div className="stat-bar-header">
                    <span>{bar.label}</span>
                    <span>{bar.value}%</span>
                  </div>
                  <div className="stat-bar-track">
                    <div className="stat-bar-fill" style={{ width: `${bar.value}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="widget-muted">Analyze a repository to see stats.</p>
        )}
      </section>

      <section className="widget-card">
        <h3 className="widget-title">Recent Activity</h3>
        {recentActivity.length === 0 ? (
          <p className="widget-muted">No activity yet.</p>
        ) : (
          <ul className="activity-list">
            {recentActivity.map((item, i) => (
              <li key={i}>
                <span className={`activity-dot ${item.ok ? "ok" : ""}`} />
                <div className="activity-body">
                  <span>{item.label}</span>
                  <time>{item.time}</time>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  );
}
