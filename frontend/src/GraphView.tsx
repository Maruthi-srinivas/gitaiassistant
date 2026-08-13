import { useEffect, useMemo, useState } from "react";
import dagre from "@dagrejs/dagre";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
  useReactFlow,
  ReactFlowProvider,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { GraphData } from "./api";
import {
  DEFAULT_ENABLED_TYPES,
  EDGE_COLORS,
  EDGE_TYPES,
  NODE_COLORS,
  edgeColor,
  filterEdges,
  neighborhood,
  rankByDegree,
  shortLabel,
} from "./graphUtils";

type Props = {
  data: GraphData | null;
  onOpenFile?: (fileId: string, name: string) => void;
  onClose?: () => void;
  fullscreen?: boolean;
};

const NODE_W = 160;
const NODE_H = 52;

function applyDagreLayout(
  rfNodes: Node[],
  rfEdges: Edge[]
): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 80, marginx: 24, marginy: 24 });

  for (const n of rfNodes) {
    g.setNode(n.id, { width: NODE_W, height: NODE_H });
  }
  for (const e of rfEdges) {
    g.setEdge(e.source, e.target);
  }
  dagre.layout(g);

  return rfNodes.map((n) => {
    const pos = g.node(n.id);
    return {
      ...n,
      position: {
        x: (pos?.x || 0) - NODE_W / 2,
        y: (pos?.y || 0) - NODE_H / 2,
      },
    };
  });
}

function FitViewOnChange({ deps }: { deps: string }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const t = setTimeout(() => fitView({ padding: 0.15, duration: 300 }), 50);
    return () => clearTimeout(t);
  }, [deps, fitView]);
  return null;
}

function GraphCanvas({
  data,
  onOpenFile,
  onClose,
}: Props) {
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [query, setQuery] = useState("");
  const [focusId, setFocusId] = useState<string | null>(null);
  const [hops, setHops] = useState<1 | 2>(1);
  const [hideExternal, setHideExternal] = useState(true);
  const [enabledTypes, setEnabledTypes] = useState<Set<string>>(
    () => new Set(DEFAULT_ENABLED_TYPES)
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<GraphData["nodes"]>([]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const nodeById = useMemo(() => {
    const m = new Map<string, GraphData["nodes"][number]>();
    for (const n of data?.nodes || []) m.set(n.id, n);
    return m;
  }, [data]);

  const availableTypes = useMemo(() => {
    const present = new Set<string>();
    for (const e of data?.edges || []) present.add(e.type);
    const ordered = EDGE_TYPES.filter((t) => present.has(t));
    for (const t of present) {
      if (!ordered.includes(t as (typeof EDGE_TYPES)[number])) ordered.push(t as (typeof EDGE_TYPES)[number]);
    }
    return ordered.length ? ordered : [...EDGE_TYPES];
  }, [data]);

  const filteredEdges = useMemo(() => {
    if (!data) return [];
    return filterEdges(data.edges, enabledTypes, hideExternal, nodeById);
  }, [data, enabledTypes, hideExternal, nodeById]);

  const visibleNodeIds = useMemo(() => {
    if (!data) return new Set<string>();
    if (focusId) {
      return neighborhood(focusId, hops, filteredEdges);
    }
    const ranked = rankByDegree(data.nodes, filteredEdges).filter((n) => {
      if (hideExternal && n.type === "EXTERNAL") return false;
      return true;
    });
    // Prefer nodes that appear in filtered edges
    const touched = new Set<string>();
    for (const e of filteredEdges) {
      touched.add(e.source_node_id);
      touched.add(e.target_node_id);
    }
    const hubs = ranked.filter((n) => touched.has(n.id)).slice(0, 20);
    if (hubs.length) return new Set(hubs.map((n) => n.id));
    return new Set(ranked.slice(0, 20).map((n) => n.id));
  }, [data, focusId, hops, filteredEdges, hideExternal]);

  const { rfNodes, rfEdges, neighborSet } = useMemo(() => {
    if (!data) return { rfNodes: [] as Node[], rfEdges: [] as Edge[], neighborSet: new Set<string>() };

    const ids = visibleNodeIds;
    const subEdges = filteredEdges.filter(
      (e) => ids.has(e.source_node_id) && ids.has(e.target_node_id)
    );

    let highlightNeighbors = new Set<string>();
    if (selectedId) {
      highlightNeighbors = neighborhood(selectedId, 1, subEdges);
    }

    const nodes: Node[] = [...ids]
      .map((id) => nodeById.get(id))
      .filter(Boolean)
      .map((n) => {
        const colors = NODE_COLORS[n!.type] || NODE_COLORS.SYMBOL;
        const dimmed =
          selectedId && !highlightNeighbors.has(n!.id) && n!.id !== selectedId;
        const isFocus = n!.id === focusId || n!.id === selectedId;
        return {
          id: n!.id,
          position: { x: 0, y: 0 },
          data: {
            label: shortLabel(n!.name),
            fullName: n!.name,
            type: n!.type,
            fileId: n!.file_id,
          },
          style: {
            background: colors.bg,
            color: "#e7eef7",
            border: `${isFocus ? 2 : 1}px solid ${colors.border}`,
            borderRadius: n!.type === "CLASS" ? 6 : 12,
            fontSize: 12,
            width: NODE_W,
            height: NODE_H,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            textAlign: "center" as const,
            padding: 6,
            opacity: dimmed ? 0.25 : 1,
            boxShadow: isFocus ? `0 0 0 2px ${colors.border}55` : "none",
            whiteSpace: "pre-wrap" as const,
            cursor: "pointer",
          },
          title: `${n!.name} (${n!.type})`,
        } as Node;
      });

    const edges: Edge[] = subEdges.map((e) => {
      const color = edgeColor(e.type);
      const dimmed =
        selectedId &&
        e.source_node_id !== selectedId &&
        e.target_node_id !== selectedId &&
        !highlightNeighbors.has(e.source_node_id) &&
        !highlightNeighbors.has(e.target_node_id);
      const dashed = e.type === "IMPORTS" || e.type === "EXTERNAL";
      return {
        id: e.id,
        source: e.source_node_id,
        target: e.target_node_id,
        label: e.type,
        markerEnd: { type: MarkerType.ArrowClosed, color },
        style: {
          stroke: color,
          strokeWidth: selectedId && (e.source_node_id === selectedId || e.target_node_id === selectedId) ? 2.5 : 1.5,
          opacity: dimmed ? 0.15 : 0.9,
          strokeDasharray: dashed ? "6 4" : undefined,
        },
        labelStyle: { fill: "#9db0c5", fontSize: 10, opacity: dimmed ? 0.2 : 0.85 },
        labelBgStyle: { fill: "#1a222c", fillOpacity: 0.85 },
      };
    });

    const laidOut = applyDagreLayout(nodes, edges);
    return { rfNodes: laidOut, rfEdges: edges, neighborSet: highlightNeighbors };
  }, [data, visibleNodeIds, filteredEdges, nodeById, selectedId, focusId]);

  const layoutKey = useMemo(
    () =>
      `${focusId || "hubs"}:${hops}:${[...enabledTypes].sort().join(",")}:${hideExternal}:${rfNodes.length}:${rfEdges.length}`,
    [focusId, hops, enabledTypes, hideExternal, rfNodes.length, rfEdges.length]
  );

  function onQueryChange(value: string) {
    setQuery(value);
    if (!data || value.trim().length < 1) {
      setSuggestions([]);
      return;
    }
    const q = value.toLowerCase();
    const matches = data.nodes
      .filter((n) => n.name.toLowerCase().includes(q))
      .slice(0, 12);
    setSuggestions(matches);
  }

  function selectFocus(node: GraphData["nodes"][number]) {
    setFocusId(node.id);
    setQuery(shortLabel(node.name));
    setSuggestions([]);
    setSelectedId(node.id);
  }

  function toggleType(t: string) {
    setEnabledTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  }

  function clearFocus() {
    setFocusId(null);
    setQuery("");
    setSuggestions([]);
  }

  const selectedNode = selectedId ? nodeById.get(selectedId) : null;

  if (!data) {
    return (
      <div className="graph-fullscreen">
        <div className="graph-topbar">
          <h2>Dependency Graph</h2>
          <button type="button" className="modal-close" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="graph-empty-full">Graph will appear after indexing.</div>
      </div>
    );
  }

  return (
    <div className={`graph-fullscreen ${railCollapsed ? "rail-collapsed" : ""}`}>
      <div className="graph-topbar">
        <div className="graph-topbar-left">
          <h2>Dependency Graph</h2>
          <span className="graph-counts">
            {rfNodes.length} nodes · {rfEdges.length} edges
            {focusId ? " · focused" : " · hub overview"}
          </span>
        </div>
        <div className="graph-topbar-actions">
          <button
            type="button"
            className="modal-close secondary-btn"
            onClick={() => setRailCollapsed((c) => !c)}
            title={railCollapsed ? "Expand controls" : "Collapse controls"}
          >
            {railCollapsed ? "Show controls" : "Hide controls"}
          </button>
          <button type="button" className="modal-close" onClick={onClose}>
            Close
          </button>
        </div>
      </div>

      <div className="graph-body">
        <aside className={`graph-rail ${railCollapsed ? "collapsed" : ""}`}>
          {!railCollapsed && (
            <>
              <div className="rail-section">
                <label className="rail-label">Search symbol</label>
                <input
                  className="rail-input"
                  value={query}
                  onChange={(e) => onQueryChange(e.target.value)}
                  placeholder="e.g. AuthService"
                />
                {suggestions.length > 0 && (
                  <ul className="rail-suggestions">
                    {suggestions.map((s) => (
                      <li key={s.id}>
                        <button type="button" onClick={() => selectFocus(s)}>
                          <span>{shortLabel(s.name)}</span>
                          <span className="sug-meta">{s.type}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {focusId && (
                  <button type="button" className="focus-chip" onClick={clearFocus}>
                    Focus: {shortLabel(nodeById.get(focusId)?.name || "")} ×
                  </button>
                )}
              </div>

              <div className="rail-section">
                <label className="rail-label">Neighborhood hops</label>
                <div className="rail-row">
                  <button
                    type="button"
                    className={hops === 1 ? "chip active" : "chip"}
                    onClick={() => setHops(1)}
                  >
                    1 hop
                  </button>
                  <button
                    type="button"
                    className={hops === 2 ? "chip active" : "chip"}
                    onClick={() => setHops(2)}
                  >
                    2 hops
                  </button>
                </div>
              </div>

              <div className="rail-section">
                <label className="rail-label">Edge types</label>
                <div className="rail-checks">
                  {availableTypes.map((t) => (
                    <label key={t} className="rail-check">
                      <input
                        type="checkbox"
                        checked={enabledTypes.has(t)}
                        onChange={() => toggleType(t)}
                      />
                      <span className="swatch" style={{ background: EDGE_COLORS[t] || "#6f859c" }} />
                      {t}
                    </label>
                  ))}
                </div>
                <label className="rail-check">
                  <input
                    type="checkbox"
                    checked={hideExternal}
                    onChange={(e) => setHideExternal(e.target.checked)}
                  />
                  Hide EXTERNAL nodes
                </label>
              </div>

              <div className="rail-section">
                <label className="rail-label">Legend</label>
                <ul className="legend-list">
                  {Object.entries(EDGE_COLORS).map(([k, c]) => (
                    <li key={k}>
                      <span className="legend-line" style={{ background: c }} />
                      {k}
                    </li>
                  ))}
                </ul>
              </div>

              {selectedNode && (
                <div className="rail-section selected-card">
                  <label className="rail-label">Selected</label>
                  <div className="selected-name">{selectedNode.name}</div>
                  <div className="sug-meta">{selectedNode.type}</div>
                  {selectedNode.file_id && onOpenFile && (
                    <button
                      type="button"
                      className="chip active"
                      onClick={() => onOpenFile(selectedNode.file_id!, selectedNode.name)}
                    >
                      Open source
                    </button>
                  )}
                  <div className="sug-meta">{neighborSet.size} neighbors highlighted</div>
                </div>
              )}
            </>
          )}
          {railCollapsed && (
            <button
              type="button"
              className="rail-expand-tab"
              onClick={() => setRailCollapsed(false)}
              title="Expand controls"
            >
              »»
            </button>
          )}
        </aside>

        <div className="graph-canvas">
          {rfNodes.length === 0 ? (
            <div className="graph-empty-full">
              No edges match these filters—enable IMPORTS or clear focus.
            </div>
          ) : (
            <ReactFlow
              nodes={rfNodes}
              edges={rfEdges}
              fitView
              onNodeClick={(_, node) => setSelectedId(node.id)}
              onPaneClick={() => setSelectedId(null)}
              minZoom={0.2}
              maxZoom={1.8}
            >
              <FitViewOnChange deps={layoutKey} />
              <Background />
              <MiniMap
                nodeColor={(n) => {
                  const t = (n.data as { type?: string })?.type || "SYMBOL";
                  return NODE_COLORS[t]?.border || "#6f859c";
                }}
                maskColor="rgba(10,14,20,0.7)"
              />
              <Controls />
            </ReactFlow>
          )}
        </div>
      </div>
    </div>
  );
}

export default function GraphView(props: Props) {
  if (props.fullscreen) {
    return (
      <ReactFlowProvider>
        <GraphCanvas {...props} />
      </ReactFlowProvider>
    );
  }
  // Non-fullscreen fallback (unused for graph now, but safe)
  return (
    <ReactFlowProvider>
      <div className="graph-wrap">
        <GraphCanvas {...props} />
      </div>
    </ReactFlowProvider>
  );
}
