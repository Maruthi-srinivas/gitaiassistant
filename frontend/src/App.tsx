import { useEffect, useMemo, useState } from "react";
import {
  api,
  formatUserError,
  type ChatResponse,
  type FileDetail,
  type FileRow,
  type GraphData,
  type IndexStatus,
  type KnowledgeNode,
  type Repo,
} from "./api";
import GraphView from "./GraphView";
import KnowledgeTree from "./KnowledgeTree";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  sources?: ChatResponse["sources"];
};

type ModalKind = null | "tree" | "graph" | "source";

function normalizeUrl(value: string): string {
  return value.trim().replace(/\.git$/, "").replace(/\/$/, "");
}

export default function App() {
  const [url, setUrl] = useState("https://github.com/pallets/flask");
  const [repo, setRepo] = useState<Repo | null>(null);
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tree, setTree] = useState<KnowledgeNode[]>([]);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [files, setFiles] = useState<FileRow[]>([]);
  const [selectedFile, setSelectedFile] = useState<FileDetail | null>(null);
  const [highlight, setHighlight] = useState<{ start: number; end: number } | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("Where is the application factory defined?");
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [openModal, setOpenModal] = useState<ModalKind>(null);

  const completed = status?.status === "COMPLETED";
  const canIncremental = Boolean(repo && completed && normalizeUrl(repo.github_url) === normalizeUrl(url));

  useEffect(() => {
    if (!repo) return;
    if (!status || status.status === "COMPLETED" || status.status === "FAILED") return;
    const t = setInterval(async () => {
      try {
        const s = await api.indexStatus(repo.id);
        setStatus(s);
        if (s.status === "COMPLETED") {
          await loadArtifacts(repo.id);
          setError(null);
        } else if (s.status === "FAILED") {
          setError(
            s.error ||
              "Indexing failed. Click Analyze to run a full re-index of this repository."
          );
        }
      } catch (e) {
        setError(formatUserError(e, "status"));
      }
    }, 2000);
    return () => clearInterval(t);
  }, [repo, status?.status]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpenModal(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function loadArtifacts(repoId: string) {
    const [t, g, f] = await Promise.all([api.tree(repoId), api.graph(repoId), api.files(repoId)]);
    setTree(t);
    setGraph(g);
    setFiles(f);
  }

  async function analyze(incremental = false) {
    setBusy(true);
    setError(null);
    setOpenModal(null);

    if (incremental) {
      if (!repo) {
        setError("Incremental needs a repository that was already analyzed. Click Analyze first.");
        setBusy(false);
        return;
      }
      if (!completed) {
        setError(
          "Incremental is only available after a successful Analyze. Wait for COMPLETED, or click Analyze."
        );
        setBusy(false);
        return;
      }
      if (normalizeUrl(repo.github_url) !== normalizeUrl(url)) {
        setError(
          "URL changed since the last Analyze. Click Analyze to index this repository first; Incremental only updates the already-analyzed repo."
        );
        setBusy(false);
        return;
      }
    }

    try {
      let current = repo;
      if (!current || normalizeUrl(current.github_url) !== normalizeUrl(url)) {
        current = await api.createRepo(url);
        setRepo(current);
        setMessages([]);
        setConversationId(undefined);
        setTree([]);
        setGraph(null);
        setFiles([]);
        setSelectedFile(null);
      }
      await api.indexRepo(current.id, incremental);
      const s = await api.indexStatus(current.id);
      setStatus(s);
      if (s.status === "FAILED") {
        setError(
          s.error ||
            (incremental
              ? "Incremental update failed. Click Analyze for a full re-index."
              : "Analyze failed. Check the repository URL and try again.")
        );
      }
    } catch (e) {
      setError(formatUserError(e, incremental ? "incremental" : "analyze"));
    } finally {
      setBusy(false);
    }
  }

  async function openPath(path: string, start?: number, end?: number) {
    const match = files.find((f) => f.path === path);
    if (!match || !repo) return;
    try {
      const detail = await api.file(repo.id, match.id);
      setSelectedFile(detail);
      setHighlight(start && end ? { start, end } : null);
      setOpenModal("source");
    } catch (e) {
      setError(formatUserError(e));
    }
  }

  async function sendChat() {
    if (!repo || !chatInput.trim() || !completed) return;
    const q = chatInput.trim();
    setChatInput("");
    setMessages((m) => [...m, { role: "user", content: q }]);
    try {
      const res = await api.chat(repo.id, q, conversationId);
      setConversationId(res.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: res.answer, sources: res.sources }]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "assistant", content: formatUserError(e, "chat") },
      ]);
    }
  }

  async function openFileById(fileId: string, _name?: string) {
    const match = files.find((f) => f.id === fileId);
    if (!match) {
      setError("No source file linked to this graph node.");
      return;
    }
    await openPath(match.path);
  }

  const lines = useMemo(() => (selectedFile?.content || "").split("\n"), [selectedFile]);

  const modalTitle =
    openModal === "tree"
      ? "Knowledge Tree"
      : openModal === "source"
        ? selectedFile
          ? `Source — ${selectedFile.path}`
          : "Source Viewer"
        : "";

  return (
    <div className="app">
      <header className="header">
        <h1>GitHub Repository AI Assistant</h1>
        <p>Paste a public GitHub URL, analyze the repo, then chat with citations.</p>
      </header>

      <div className="analyze-bar">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://github.com/owner/repo"
        />
        <button disabled={busy} onClick={() => analyze(false)}>
          Analyze
        </button>
        <button className="secondary" disabled={busy || !canIncremental} onClick={() => analyze(true)} title={!canIncremental ? "Analyze this repo successfully first, then Incremental can update it" : "Update only changed files since last index"}>
          Incremental
        </button>
      </div>

      <div className={`status-line ${error ? "error" : ""}`}>
        {error
          ? error
          : status
            ? `Repo ${repo?.owner}/${repo?.name} — ${status.status} (${Math.round((status.progress || 0) * 100)}%)`
            : "Ready"}
      </div>

      {completed && (
        <div className="explore-actions">
          <button type="button" onClick={() => setOpenModal("tree")}>
            Knowledge Tree
          </button>
          <button type="button" onClick={() => setOpenModal("graph")}>
            Dependency Graph
          </button>
        </div>
      )}

      <div className="workspace">
        {!completed ? (
          <div className="workspace-idle">
            {status && status.status !== "FAILED" ? (
              <>
                <h2>Indexing in progress</h2>
                <p>Chat and explore tools unlock when analysis finishes.</p>
              </>
            ) : status?.status === "FAILED" ? (
              <>
                <h2>Indexing failed</h2>
                <p>
                  {status.error ||
                    error ||
                    "Something went wrong during indexing."}
                </p>
                <p className="idle-hint">Click Analyze to run a full re-index of this repository.</p>
              </>
            ) : (
              <>
                <h2>Start by analyzing a repository</h2>
                <p>Paste a public GitHub URL above and click Analyze.</p>
              </>
            )}
          </div>
        ) : (
          <section className="chat-panel">
            <div className="chat-panel-header">
              <h2>Chat</h2>
              <span className="chat-repo">
                {repo?.owner}/{repo?.name}
              </span>
            </div>
            <div className="chat">
              <div className="messages">
                {messages.length === 0 && (
                  <div className="chat-empty">Ask a question about this repository. Citations open the source viewer.</div>
                )}
                {messages.map((m, i) => (
                  <div key={i} className={`bubble ${m.role}`}>
                    {m.content}
                    {m.sources && m.sources.length > 0 && (
                      <div className="sources">
                        {m.sources.map((s, j) => (
                          <button
                            key={j}
                            onClick={() => openPath(s.file, s.start_line, s.end_line)}
                            title={s.snippet || ""}
                          >
                            {s.file}:{s.start_line}-{s.end_line}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="chat-input">
                <input
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && sendChat()}
                  placeholder="Ask about this repository..."
                />
                <button onClick={sendChat}>Send</button>
              </div>
            </div>
          </section>
        )}
      </div>

      {openModal === "graph" && (
        <GraphView
          data={graph}
          fullscreen
          onClose={() => setOpenModal(null)}
          onOpenFile={openFileById}
        />
      )}

      {openModal && openModal !== "graph" && (
        <div className="modal-backdrop" onClick={() => setOpenModal(null)} role="presentation">
          <div
            className={`modal ${openModal === "source" ? "modal-source" : "modal-wide"}`}
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={modalTitle}
          >
            <div className="modal-header">
              <h2>{modalTitle}</h2>
              <button type="button" className="modal-close" onClick={() => setOpenModal(null)}>
                Close
              </button>
            </div>
            <div className="modal-body">
              {openModal === "tree" && (
                <KnowledgeTree nodes={tree} onSelectFile={(p) => openPath(p)} />
              )}
              {openModal === "source" && (
                <div className="source-view">
                  {!selectedFile ? (
                    <div>Select a file from the tree or a citation.</div>
                  ) : (
                    <pre>
                      {lines.map((line, idx) => {
                        const n = idx + 1;
                        const active =
                          highlight && n >= highlight.start && n <= highlight.end ? " highlight" : "";
                        return (
                          <div key={n} className={`line${active}`}>
                            {String(n).padStart(4, " ")} | {line}
                          </div>
                        );
                      })}
                    </pre>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
