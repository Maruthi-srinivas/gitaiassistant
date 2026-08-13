import type { IndexStatus, KnowledgeNode, Repo } from "./api";
import KnowledgeTree from "./KnowledgeTree";

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
};

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
}: Props) {
  if (collapsed) {
    return (
      <div className="left-rail-inner collapsed-inner">
        <button
          type="button"
          className="rail-icon-btn"
          onClick={onToggleCollapse}
          title="Expand tools"
        >
          »»
        </button>
        {completed && (
          <button
            type="button"
            className="rail-icon-btn"
            onClick={onOpenGraph}
            title="Dependency Graph"
          >
            G
          </button>
        )}
      </div>
    );
  }

  const statusText = error
    ? error
    : status
      ? `${repo?.owner}/${repo?.name} — ${status.status} (${Math.round((status.progress || 0) * 100)}%)`
      : "Ready";

  return (
    <div className="left-rail-inner">
      <div className="left-rail-header">
        <span className="rail-label">Repository</span>
        <button
          type="button"
          className="rail-collapse-btn"
          onClick={onToggleCollapse}
          title="Collapse tools"
        >
          ««
        </button>
      </div>

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
                ? "Analyze this repo successfully first, then Incremental can update it"
                : "Update only changed files since last index"
            }
          >
            Incremental
          </button>
        </div>
      </div>

      <div className={`rail-status ${error ? "error" : ""}`}>{statusText}</div>

      {completed && (
        <>
          <div className="rail-tools">
            <button type="button" className="rail-tool-btn" onClick={onOpenGraph}>
              Dependency Graph
            </button>
          </div>

          <div className="rail-tree-section">
            <span className="rail-label">Knowledge Tree</span>
            <div className="rail-tree-scroll">
              <KnowledgeTree nodes={tree} onSelectFile={onSelectFile} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
