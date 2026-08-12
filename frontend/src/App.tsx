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

  async function loadArtifacts(repoId: string) {
    const [t, g, f] = await Promise.all([api.tree(repoId), api.graph(repoId), api.files(repoId)]);
    setTree(t);
    setGraph(g);
    setFiles(f);
  }

  async function analyze(incremental = false) {
    setBusy(true);
    setError(null);
    try {
      let current = repo;
      if (!current || current.github_url !== url.replace(/\.git$/, "").replace(/\/$/, "")) {
        current = await api.createRepo(url);
        setRepo(current);
        setMessages([]);
        setConversationId(undefined);
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
  }

  async function sendChat() {
    if (!repo || !chatInput.trim()) return;
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

  return (
    <div className="app">
      <header className="header">
        <h1>GitHub Repository AI Assistant</h1>
        <p>Paste a public GitHub URL, index the code, explore the tree/graph, and ask cited questions.</p>
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

      <div className="main">
        <section className="panel">
          <h2>Knowledge Tree</h2>
          <div className="panel-body">
            <KnowledgeTree nodes={tree} onSelectFile={(p) => openPath(p)} />
          </div>
        </section>

        <section className="panel">
          <h2>Dependency Graph</h2>
          <GraphView data={graph} />
        </section>

        <section className="panel">
          <h2>Chat</h2>
          <div className="panel-body chat">
            <div className="messages">
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
                disabled={!repo || status?.status !== "COMPLETED"}
              />
              <button onClick={sendChat} disabled={!repo || status?.status !== "COMPLETED"}>
                Send
              </button>
            </div>
          </div>
        </section>

        <section className="panel">
          <h2>Source Viewer {selectedFile ? `— ${selectedFile.path}` : ""}</h2>
          <div className="panel-body source-view">
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
        </section>
      </div>
    </div>
  );
}
