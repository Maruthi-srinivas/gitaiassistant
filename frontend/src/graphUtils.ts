import type { GraphData } from "./api";

export type GraphNode = GraphData["nodes"][number];
export type GraphEdge = GraphData["edges"][number];

export const EDGE_TYPES = [
  "CALLS",
  "EXTENDS",
  "IMPLEMENTS",
  "DEPENDS_ON",
  "USES",
  "IMPORTS",
  "EXTERNAL",
  "INJECTS",
  "PUBLISHES",
  "CONSUMES",
  "CONTAINS",
] as const;

export type EdgeType = (typeof EDGE_TYPES)[number] | string;

export const DEFAULT_ENABLED_TYPES = new Set<string>([
  "CALLS",
  "EXTENDS",
  "IMPLEMENTS",
  "DEPENDS_ON",
  "USES",
  "INJECTS",
  "PUBLISHES",
  "CONSUMES",
  "CONTAINS",
]);

export const EDGE_COLORS: Record<string, string> = {
  CALLS: "#3d9cf0",
  EXTENDS: "#b07cff",
  IMPLEMENTS: "#9b6dff",
  DEPENDS_ON: "#f0a35d",
  USES: "#e0c35a",
  IMPORTS: "#6f859c",
  EXTERNAL: "#4a5562",
  INJECTS: "#2bb673",
  PUBLISHES: "#f59e0b",
  CONSUMES: "#fb7185",
  CONTAINS: "#7ec8c8",
};

export const NODE_COLORS: Record<string, { bg: string; border: string }> = {
  CLASS: { bg: "#243447", border: "#3d9cf0" },
  FUNCTION: { bg: "#1e2f28", border: "#2bb673" },
  METHOD: { bg: "#1e2f28", border: "#5ecf90" },
  MODULE: { bg: "#2a2438", border: "#b07cff" },
  SYMBOL: { bg: "#243040", border: "#6f859c" },
  EXTERNAL: { bg: "#1a1f26", border: "#4a5562" },
};

export function shortLabel(name: string): string {
  const parts = name.split(/[./]/);
  return parts[parts.length - 1] || name;
}

export function edgeColor(type: string): string {
  return EDGE_COLORS[type] || "#6f859c";
}

export function isDashedEdge(type: string, nodeType?: string): boolean {
  return type === "IMPORTS" || nodeType === "EXTERNAL";
}

export function buildAdjacency(edges: GraphEdge[]): {
  out: Map<string, Set<string>>;
  inn: Map<string, Set<string>>;
} {
  const out = new Map<string, Set<string>>();
  const inn = new Map<string, Set<string>>();
  for (const e of edges) {
    if (!out.has(e.source_node_id)) out.set(e.source_node_id, new Set());
    if (!inn.has(e.target_node_id)) inn.set(e.target_node_id, new Set());
    out.get(e.source_node_id)!.add(e.target_node_id);
    inn.get(e.target_node_id)!.add(e.source_node_id);
  }
  return { out, inn };
}

export function rankByDegree(nodes: GraphNode[], edges: GraphEdge[]): GraphNode[] {
  const deg = new Map<string, number>();
  for (const n of nodes) deg.set(n.id, 0);
  for (const e of edges) {
    deg.set(e.source_node_id, (deg.get(e.source_node_id) || 0) + 1);
    deg.set(e.target_node_id, (deg.get(e.target_node_id) || 0) + 1);
  }
  return [...nodes].sort((a, b) => (deg.get(b.id) || 0) - (deg.get(a.id) || 0));
}

export function filterEdges(
  edges: GraphEdge[],
  enabledTypes: Set<string>,
  hideExternal: boolean,
  nodeById: Map<string, GraphNode>
): GraphEdge[] {
  return edges.filter((e) => {
    if (!enabledTypes.has(e.type)) return false;
    if (hideExternal) {
      const s = nodeById.get(e.source_node_id);
      const t = nodeById.get(e.target_node_id);
      if (s?.type === "EXTERNAL" || t?.type === "EXTERNAL") return false;
    }
    return true;
  });
}

export function neighborhood(
  seedId: string,
  hops: number,
  edges: GraphEdge[]
): Set<string> {
  const { out, inn } = buildAdjacency(edges);
  let frontier = new Set<string>([seedId]);
  const seen = new Set<string>([seedId]);
  for (let h = 0; h < hops; h++) {
    const next = new Set<string>();
    for (const id of frontier) {
      for (const n of out.get(id) || []) {
        if (!seen.has(n)) {
          seen.add(n);
          next.add(n);
        }
      }
      for (const n of inn.get(id) || []) {
        if (!seen.has(n)) {
          seen.add(n);
          next.add(n);
        }
      }
    }
    frontier = next;
    if (!frontier.size) break;
  }
  return seen;
}
