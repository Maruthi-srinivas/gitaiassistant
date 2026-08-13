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
}: Props) {
  if (!completed) {
    return (
      <div className="chat-column-inner">
        <div className="workspace-idle">
          {indexing ? (
            <>
              <h2>Indexing in progress</h2>
              <p>Chat and explore tools unlock when analysis finishes.</p>
            </>
          ) : failed ? (
            <>
              <h2>Indexing failed</h2>
              <p>{failMessage || "Something went wrong during indexing."}</p>
              <p className="idle-hint">Click Analyze to run a full re-index of this repository.</p>
            </>
          ) : (
            <>
              <h2>Start by analyzing a repository</h2>
              <p>Paste a public GitHub URL in the left rail and click Analyze.</p>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="chat-column-inner">
      <div className="chat-panel-header">
        <h2>Chat</h2>
        {repo && (
          <span className="chat-repo">
            {repo.owner}/{repo.name}
          </span>
        )}
      </div>
      <div className="chat">
        <div className="messages">
          {messages.length === 0 && (
            <div className="chat-empty">
              Ask about architecture, entry points, or how a symbol is used. Citations open the
              source beside this chat.
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`bubble ${m.role}`}>
              {m.content}
              {m.sources && m.sources.length > 0 && (
                <div className="sources">
                  {m.sources.map((s, j) => (
                    <button
                      key={j}
                      type="button"
                      onClick={() => onOpenCitation(s.file, s.start_line, s.end_line)}
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
            onChange={(e) => onChatInputChange(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onSend()}
            placeholder="Ask about this repository..."
          />
          <button type="button" onClick={onSend}>
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
