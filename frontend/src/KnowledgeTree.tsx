import type { KnowledgeNode } from "./api";

type Props = {
  nodes: KnowledgeNode[];
  onSelectFile?: (path: string) => void;
};

function TreeNode({ node, onSelectFile }: { node: KnowledgeNode; onSelectFile?: (path: string) => void }) {
  const clickable = node.type === "file" && node.path;
  return (
    <li>
      {clickable ? (
        <button className="linkish" onClick={() => onSelectFile?.(node.path!)}>
          {node.name}
        </button>
      ) : (
        <span>{node.name}</span>
      )}
      <span className="meta">{node.type}</span>
      {node.children?.length > 0 && (
        <ul>
          {node.children.map((c) => (
            <TreeNode key={c.id} node={c} onSelectFile={onSelectFile} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function KnowledgeTree({ nodes, onSelectFile }: Props) {
  if (!nodes.length) {
    return <div>Knowledge tree will appear after indexing.</div>;
  }
  return (
    <div className="tree">
      <ul>
        {nodes.map((n) => (
          <TreeNode key={n.id} node={n} onSelectFile={onSelectFile} />
        ))}
      </ul>
    </div>
  );
}
