const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export type Repo = {
  id: string;
  github_url: string;
  owner: string;
  name: string;
  status: string;
  error?: string | null;
};

export type IndexStatus = {
  repository_id: string;
  status: string;
  progress: number;
  error?: string | null;
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

export const api = {
  createRepo: (url: string) =>
    request<Repo>("/api/repositories", { method: "POST", body: JSON.stringify({ url }) }),
  listRepos: () => request<Repo[]>("/api/repositories"),
  indexRepo: (id: string, incremental = false) =>
    request<{ job_id: string; status: string }>(`/api/repositories/${id}/index`, {
      method: "POST",
      body: JSON.stringify({ incremental }),
    }),
  indexStatus: (id: string) => request<IndexStatus>(`/api/repositories/${id}/index-status`),
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
