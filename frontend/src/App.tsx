import { useEffect, useMemo, useState } from "react";
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
import RightSidebar from "./RightSidebar";
import SourceDrawer from "./SourceDrawer";
import TopHeader from "./TopHeader";
import WorkspaceShell from "./WorkspaceShell";

function normalizeUrl(value: string): string {
  return value.trim().replace(/\.git$/, "").replace(/\/$/, "");
}

type NavItem = "home" | "repositories" | "chats" | "knowledge" | "settings";

export default function App() {
  const [url, setUrl] = useState("https://github.com/pallets/flask");
  const [repo, setRepo] = useState<Repo | null>(null);
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [chatBusy, setChatBusy] = useState(false);
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
  const [activeNav, setActiveNav] = useState<NavItem>("home");
  const [leftCollapsed, setLeftCollapsed] = useState(
    () => typeof window !== "undefined" && window.innerWidth <= 960
  );
  const [activityLog, setActivityLog] = useState<
    { label: string; time: string; ok: boolean }[]
  >([]);

  const completed = status?.status === "COMPLETED";
  const canIncremental = Boolean(
    repo && completed && normalizeUrl(repo.github_url) === normalizeUrl(url)
  );

  const recentChats = useMemo(() => {
    const titles: { id: string; title: string; active?: boolean }[] = [];
    messages.forEach((m, i) => {
      if (m.role === "user") {
        titles.push({
          id: String(i),
          title: m.content.length > 48 ? `${m.content.slice(0, 48)}…` : m.content,
          active: i === messages.length - 2 || (messages.length === 1 && i === 0),
        });
      }
    });
    return titles.slice(-5).reverse();
  }, [messages]);

  const lastSourceCount = useMemo(() => {
    const last = [...messages].reverse().find((m) => m.role === "assistant");
    return last?.sources?.length ?? 0;
  }, [messages]);

  function logActivity(label: string, ok = true) {
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setActivityLog((prev) => [{ label, time, ok }, ...prev].slice(0, 8));
  }

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
          logActivity("Repository indexed successfully");
        } else if (s.status === "FAILED") {
          setError(
            s.error ||
              "Indexing failed. Click Analyze to run a full re-index of this repository."
          );
          logActivity("Indexing failed", false);
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
        logActivity(`Connected ${current.owner}/${current.name}`);
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
        logActivity("Analyze failed", false);
      } else if (s.status !== "COMPLETED") {
        logActivity(incremental ? "Incremental update started" : "Analyze started");
      }
    } catch (e) {
      setError(formatUserError(e, incremental ? "incremental" : "analyze"));
      logActivity("Analyze error", false);
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
      logActivity(`Opened ${path}`);
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
    if (!repo || !chatInput.trim() || !completed || chatBusy) return;
    const q = chatInput.trim();
    setChatInput("");
    setMessages((m) => [...m, { role: "user", content: q }]);
    setChatBusy(true);
    logActivity(`Query: ${q.length > 40 ? `${q.slice(0, 40)}…` : q}`);
    try {
      const res = await api.chat(repo.id, q, conversationId);
      setConversationId(res.conversation_id);
      setMessages((m) => [
        ...m,
        { role: "assistant", content: res.answer, sources: res.sources },
      ]);
      logActivity("Response generated");
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: formatUserError(e, "chat") }]);
      logActivity("Chat error", false);
    } finally {
      setChatBusy(false);
    }
  }

  const indexing =
    Boolean(status) && status!.status !== "FAILED" && status!.status !== "COMPLETED";
  const failed = status?.status === "FAILED";

  const headerStatus = error
    ? "Agent Offline"
    : indexing
      ? "Indexing…"
      : completed
        ? "Agent Online"
        : "Ready";

  return (
    <>
      <WorkspaceShell
        leftCollapsed={leftCollapsed}
        sourceOpen={sourceOpen}
        header={
          <TopHeader
            agentOnline={completed && !error}
            statusLabel={headerStatus}
            searchValue={chatInput}
            onSearchChange={setChatInput}
            onSearchSubmit={() => {
              if (completed) sendChat();
              else {
                setActiveNav("repositories");
                analyze(false);
              }
            }}
          />
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
            activeNav={activeNav}
            onNavChange={setActiveNav}
            recentChats={recentChats}
            onSelectChat={() => setActiveNav("chats")}
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
            onConnectRepo={() => {
              setActiveNav("repositories");
              analyze(false);
            }}
            fileCount={files.length}
            chatBusy={chatBusy}
          />
        }
        right={
          <RightSidebar
            repo={repo}
            status={status}
            completed={completed}
            indexing={indexing}
            chatBusy={chatBusy}
            fileCount={files.length}
            sourceCount={lastSourceCount}
            recentActivity={activityLog}
          />
        }
        sourcePanel={
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
