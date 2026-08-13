import type { ChatResponse, Repo } from "./api";

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  sources?: ChatResponse["sources"];
};

type Props = {
  completed: boolean;
  indexing: boolean;
  failed: boolean;
  failMessage?: string | null;
  repo: Repo | null;
  messages: ChatMessage[];
  chatInput: string;
  onChatInputChange: (value: string) => void;
  onSend: () => void;
  onOpenCitation: (file: string, start: number, end: number) => void;
  onConnectRepo: () => void;
  fileCount: number;
  chatBusy: boolean;
};

export default function ChatPanel({
  completed,
  indexing,
  failed,
  failMessage,
  repo,
  messages,
  chatInput,
  onChatInputChange,
  onSend,
  onOpenCitation,
  onConnectRepo,
  fileCount,
  chatBusy,
}: Props) {
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const sourceCount = lastAssistant?.sources?.length ?? 0;

  if (!completed) {
    return (
      <div className="chat-column-inner">
        <div className="hero-card">
          <div className="hero-icon">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                fill="currentColor"
                d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"
              />
            </svg>
          </div>
          <div className="hero-text">
            <h2>GitHub AI Assistant</h2>
            <p>
              {indexing
                ? "Indexing your repository — chat unlocks when analysis finishes."
                : failed
                  ? failMessage || "Indexing failed. Try connecting again."
                  : "Connect a public GitHub repository to explore code, ask questions, and get cited answers."}
            </p>
          </div>
          {!indexing && (
            <button type="button" className="btn-primary hero-connect" onClick={onConnectRepo}>
              Connect Repository
            </button>
          )}
          {indexing && (
            <div className="hero-loading">
              <span className="spinner" />
              Indexing in progress…
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="chat-column-inner">
      <div className="repo-context-bar">
        <div className="repo-context-main">
          <svg className="repo-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="currentColor"
              d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"
            />
          </svg>
          <div>
            <span className="repo-name">
              {repo?.owner}/{repo?.name}
            </span>
            <span className="repo-branch">main</span>
          </div>
        </div>
        <div className="repo-stats-row">
          <span>{fileCount} files</span>
          <span className="stat-sep">·</span>
          <span>Indexed</span>
        </div>
      </div>

      <div className="chat">
        <div className="messages">
          {messages.length === 0 && (
            <div className="chat-empty">
              Ask about architecture, entry points, or how a symbol is used. Citations open source
              code in the panel on the right.
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`message-row ${m.role}`}>
              <div className={`message-avatar ${m.role}`}>
                {m.role === "user" ? "U" : "AI"}
              </div>
              <div className={`bubble ${m.role}`}>
                {m.content}
                {m.sources && m.sources.length > 0 && (
                  <>
                    <div className="key-files-label">Key Files</div>
                    <div className="sources">
                      {m.sources.map((s, j) => (
                        <button
                          key={j}
                          type="button"
                          onClick={() => onOpenCitation(s.file, s.start_line, s.end_line)}
                          title={s.snippet || ""}
                        >
                          {s.file}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          ))}
          {chatBusy && (
            <div className="message-row assistant">
              <div className="message-avatar assistant">AI</div>
              <div className="bubble assistant typing">
                <span className="typing-dot" />
                <span className="typing-dot" />
                <span className="typing-dot" />
              </div>
            </div>
          )}
        </div>

        {messages.length > 0 && (
          <div className="context-footer">
            <span className="context-chip ok">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path fill="none" stroke="currentColor" strokeWidth="2.5" d="M5 13l4 4L19 7" />
              </svg>
              Repository Indexed
            </span>
            {sourceCount > 0 && (
              <span className="context-chip ok">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path fill="none" stroke="currentColor" strokeWidth="2.5" d="M5 13l4 4L19 7" />
                </svg>
                {sourceCount} Relevant Files Found
              </span>
            )}
            <span className="context-chip ok">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path fill="none" stroke="currentColor" strokeWidth="2.5" d="M5 13l4 4L19 7" />
              </svg>
              Context Ready
            </span>
          </div>
        )}

        <div className="chat-input">
          <button type="button" className="attach-btn" title="Attach" aria-label="Attach file">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"
              />
            </svg>
          </button>
          <input
            value={chatInput}
            onChange={(e) => onChatInputChange(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !chatBusy && onSend()}
            placeholder="Ask about this repository..."
            disabled={chatBusy}
          />
          <button type="button" className="send-btn" onClick={onSend} disabled={chatBusy}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path fill="currentColor" d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
            </svg>
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
