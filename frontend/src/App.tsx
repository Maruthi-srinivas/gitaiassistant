import { useEffect, useState } from "react";
import {
  api,
  formatUserError,
  type FileDetail,
  type FileRow,
  type GraphData,
  type IndexStatus,
  type KnowledgeNode,
  type Repo,
} from "./api";
import ChatPanel, { type ChatMessage } from "./ChatPanel";
import GraphView from "./GraphView";
import LeftRail from "./LeftRail";
import SourceDrawer from "./SourceDrawer";
import WorkspaceShell from "./WorkspaceShell";

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
  const [graphOpen, setGraphOpen] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [leftCollapsed, setLeftCollapsed] = useState(
    () => typeof window !== "undefined" && window.innerWidth <= 960
  );

  const completed = status?.status === "COMPLETED";
  const canIncremental = Boolean(
    repo && completed && normalizeUrl(repo.github_url) === normalizeUrl(url)
  );

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
      if (e.key !== "Escape") return;
      if (graphOpen) {
        setGraphOpen(false);
        return;
      }
      if (sourceOpen) {
        setSourceOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [graphOpen, sourceOpen]);

  async function loadArtifacts(repoId: string) {
    const [t, g, f] = await Promise.all([api.tree(repoId), api.graph(repoId), api.files(repoId)]);
    setTree(t);
    setGraph(g);
    setFiles(f);
  }

  async function analyze(incremental = false) {
    setBusy(true);
    setError(null);
    setGraphOpen(false);

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
        setSourceOpen(false);
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
      setGraphOpen(false);
      setSourceOpen(true);
    } catch (e) {
      setError(formatUserError(e));
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
      setMessages((m) => [...m, { role: "assistant", content: formatUserError(e, "chat") }]);
    }
  }

  const indexing =
    Boolean(status) && status!.status !== "FAILED" && status!.status !== "COMPLETED";
  const failed = status?.status === "FAILED";

  return (
    <>
      <WorkspaceShell
        leftCollapsed={leftCollapsed}
        sourceOpen={sourceOpen}
        brand={
          <>
            <h1>GitHub Repository AI Assistant</h1>
            <span className="brand-status">
              {error
                ? error
                : status
                  ? `${status.status}`
                  : "Paste a repo URL in the left rail to begin"}
            </span>
          </>
        }
        left={
          <LeftRail
            url={url}
            onUrlChange={setUrl}
            busy={busy}
            canIncremental={canIncremental}
            onAnalyze={() => analyze(false)}
            onIncremental={() => analyze(true)}
            status={status}
            repo={repo}
            error={error}
            completed={completed}
            tree={tree}
            collapsed={leftCollapsed}
            onToggleCollapse={() => setLeftCollapsed((c) => !c)}
            onOpenGraph={() => setGraphOpen(true)}
            onSelectFile={(p) => openPath(p)}
          />
        }
        center={
          <ChatPanel
            completed={completed}
            indexing={indexing}
            failed={failed}
            failMessage={status?.error || error}
            repo={repo}
            messages={messages}
            chatInput={chatInput}
            onChatInputChange={setChatInput}
            onSend={sendChat}
            onOpenCitation={(file, start, end) => openPath(file, start, end)}
          />
        }
        right={
          <SourceDrawer
            open={sourceOpen}
            file={selectedFile}
            highlight={highlight}
            onClose={() => setSourceOpen(false)}
          />
        }
      />

      {graphOpen && (
        <GraphView
          data={graph}
          fullscreen
          onClose={() => setGraphOpen(false)}
          onOpenFile={openFileById}
        />
      )}
    </>
  );
}
