import { useEffect, useMemo, useState } from "react";
import {
  api,
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

  useEffect(() => {
    if (!repo) return;
    if (!status || status.status === "COMPLETED" || status.status === "FAILED") return;
    const t = setInterval(async () => {
      try {
        const s = await api.indexStatus(repo.id);
        setStatus(s);
        if (s.status === "COMPLETED") {
          await loadArtifacts(repo.id);
        }
      } catch (e) {
        setError(String(e));
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
    try {
      let current = repo;
      if (!current || current.github_url !== url.replace(/\.git$/, "").replace(/\/$/, "")) {
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
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function openPath(path: string, start?: number, end?: number) {
    const match = files.find((f) => f.path === path);
    if (!match || !repo) return;
    const detail = await api.file(repo.id, match.id);
    setSelectedFile(detail);
    setHighlight(start && end ? { start, end } : null);
    setOpenModal("source");
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
      setMessages((m) => [...m, { role: "assistant", content: `Error: ${e}` }]);
    }
  }

  const lines = useMemo(() => (selectedFile?.content || "").split("\n"), [selectedFile]);

  const modalTitle =
    openModal === "tree"
      ? "Knowledge Tree"
      : openModal === "graph"
        ? "Dependency Graph"
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
        <button className="secondary" disabled={busy || !repo} onClick={() => analyze(true)}>
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
                <p>{status.error || "Try analyzing again."}</p>
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

      {openModal && (
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
              {openModal === "graph" && <GraphView data={graph} />}
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
