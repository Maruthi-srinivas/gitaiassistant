const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export type Repo = {
  id: string;
  github_url: string;
  owner: string;
  name: string;
  status: string;
  default_branch?: string | null;
  error?: string | null;
};

export type IndexStatus = {
  repository_id: string;
  status: string;
  progress: number;
  error?: string | null;
  branch?: string | null;
  timings?: Record<string, number> | null;
  metrics?: Record<string, unknown> | null;
};

export type Branch = {
  name: string;
  commit_hash?: string | null;
  is_indexed: boolean;
};

export type KnowledgeNode = {
  id: string;
  name: string;
  type: string;
  description?: string | null;
  path?: string | null;
  children: KnowledgeNode[];
};

export type GraphData = {
  nodes: { id: string; type: string; name: string; file_id?: string | null }[];
  edges: { id: string; source_node_id: string; target_node_id: string; type: string }[];
};

export type FileRow = { id: string; path: string; language?: string; size: number };
export type FileDetail = FileRow & { content?: string | null };

export type ChatResponse = {
  conversation_id: string;
  answer: string;
  sources: { file: string; start_line: number; end_line: number; snippet?: string }[];
};

function parseErrorBody(text: string): string {
  if (!text) return "";
  try {
    const data = JSON.parse(text);
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail)) {
      return data.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join("; ");
    }
    if (typeof data?.message === "string") return data.message;
    if (typeof data?.error === "string") return data.error;
  } catch {
    // plain text body
  }
  return text;
}

/** Turn network / API failures into user-facing guidance. */
export function formatUserError(err: unknown, context?: "incremental" | "analyze" | "chat" | "status"): string {
  const raw = err instanceof Error ? err.message : String(err);
  const lower = raw.toLowerCase();

  if (
    lower.includes("failed to fetch") ||
    lower.includes("networkerror") ||
    lower.includes("load failed") ||
    lower.includes("network request failed")
  ) {
    if (context === "incremental") {
      return "Could not reach the API for incremental update. Check that Docker/backend is running, then try Analyze for a full refresh.";
    }
    return "Could not reach the API (network error). Make sure the backend is running at http://localhost:8000, then try again.";
  }

  if (lower.includes("failed to fetch") || lower.includes("git fetch") || lower.includes("origin.fetch")) {
    return "Git fetch failed while updating the repository. Click Analyze to run a full re-index instead.";
  }

  const cleaned = parseErrorBody(raw).trim();
  if (!cleaned) {
    return context === "incremental"
      ? "Incremental update failed. Click Analyze to re-index the repository."
      : "Request failed. Please try again.";
  }

  const short = cleaned.length > 280 ? `${cleaned.slice(0, 280)}…` : cleaned;
  if (context === "incremental") {
    return `${short} — If this keeps happening, click Analyze for a full re-index.`;
  }
  return short;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      ...init,
    });
  } catch (err) {
    throw new Error(err instanceof Error ? err.message : "Failed to fetch");
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(parseErrorBody(text) || res.statusText || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  createRepo: (url: string) =>
    request<Repo>("/api/repositories", { method: "POST", body: JSON.stringify({ url }) }),
  listRepos: () => request<Repo[]>("/api/repositories"),
  indexRepo: (id: string, incremental = false, branch?: string) =>
    request<{ job_id: string; status: string }>(`/api/repositories/${id}/index`, {
      method: "POST",
      body: JSON.stringify({ incremental, branch: branch || undefined }),
    }),
  indexStatus: (id: string) => request<IndexStatus>(`/api/repositories/${id}/index-status`),
  branches: (id: string) => request<Branch[]>(`/api/repositories/${id}/branches`),
  tree: (id: string) => request<KnowledgeNode[]>(`/api/repositories/${id}/tree`),
  graph: (id: string) => request<GraphData>(`/api/repositories/${id}/graph`),
  files: (id: string) => request<FileRow[]>(`/api/repositories/${id}/files`),
  file: (repoId: string, fileId: string) =>
    request<FileDetail>(`/api/repositories/${repoId}/files/${fileId}`),
  chat: (id: string, message: string, conversation_id?: string) =>
    request<ChatResponse>(`/api/repositories/${id}/chat`, {
      method: "POST",
      body: JSON.stringify({ message, conversation_id }),
    }),
};
