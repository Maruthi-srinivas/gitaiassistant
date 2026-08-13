import { useEffect, useMemo, useRef } from "react";
import type { FileDetail } from "./api";

type Props = {
  open: boolean;
  file: FileDetail | null;
  highlight: { start: number; end: number } | null;
  onClose: () => void;
};

export default function SourceDrawer({ open, file, highlight, onClose }: Props) {
  const highlightRef = useRef<HTMLDivElement | null>(null);
  const lines = useMemo(() => (file?.content || "").split("\n"), [file]);

  useEffect(() => {
    if (!open || !highlight) return;
    const t = setTimeout(() => {
      highlightRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    }, 50);
    return () => clearTimeout(t);
  }, [open, file?.id, highlight?.start, highlight?.end]);

  if (!open) {
    return null;
  }

  return (
    <div className="source-drawer-inner">
      <div className="source-drawer-header">
        <h2 title={file?.path || ""}>{file ? file.path : "Source"}</h2>
        <button type="button" className="modal-close" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="source-view">
        {!file ? (
          <div className="source-empty">Select a file from the tree or a citation.</div>
        ) : (
          <pre>
            {lines.map((line, idx) => {
              const n = idx + 1;
              const active = highlight && n >= highlight.start && n <= highlight.end;
              return (
                <div
                  key={n}
                  ref={active && n === highlight!.start ? highlightRef : undefined}
                  className={`line${active ? " highlight" : ""}`}
                >
                  {String(n).padStart(4, " ")} | {line}
                </div>
              );
            })}
          </pre>
        )}
      </div>
    </div>
  );
}
