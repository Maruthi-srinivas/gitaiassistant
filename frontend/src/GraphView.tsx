import { useMemo } from "react";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { GraphData } from "./api";

type Props = { data: GraphData | null };

export default function GraphView({ data }: Props) {
  const { nodes, edges } = useMemo(() => {
    if (!data) return { nodes: [] as Node[], edges: [] as Edge[] };
    const limitedNodes = data.nodes.slice(0, 80);
    const ids = new Set(limitedNodes.map((n) => n.id));
    const nodes: Node[] = limitedNodes.map((n, i) => {
      const col = i % 8;
      const row = Math.floor(i / 8);
      return {
        id: n.id,
        position: { x: col * 180, y: row * 90 },
        data: { label: `${n.name}\n(${n.type})` },
        style: {
          background: "#243040",
          color: "#e7eef7",
          border: "1px solid #3a4b5f",
          borderRadius: 8,
          fontSize: 11,
          width: 150,
          whiteSpace: "pre-wrap",
        },
      };
    });
    const edges: Edge[] = data.edges
      .filter((e) => ids.has(e.source_node_id) && ids.has(e.target_node_id))
      .slice(0, 120)
      .map((e) => ({
        id: e.id,
        source: e.source_node_id,
        target: e.target_node_id,
        label: e.type,
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: "#6f859c" },
        labelStyle: { fill: "#9db0c5", fontSize: 10 },
      }));
    return { nodes, edges };
  }, [data]);

  if (!data) {
    return <div className="graph-empty">Graph will appear after indexing.</div>;
  }

  return (
    <div className="graph-wrap">
      <ReactFlow nodes={nodes} edges={edges} fitView style={{ width: "100%", height: "100%" }}>
        <Background />
        <MiniMap />
        <Controls />
      </ReactFlow>
    </div>
  );
}
