import type { ReactNode } from "react";

type Props = {
  brand: ReactNode;
  left: ReactNode;
  center: ReactNode;
  right: ReactNode;
  leftCollapsed: boolean;
  sourceOpen: boolean;
};

export default function WorkspaceShell({
  brand,
  left,
  center,
  right,
  leftCollapsed,
  sourceOpen,
}: Props) {
  return (
    <div
      className={`workspace-shell ${leftCollapsed ? "left-collapsed" : ""} ${
        sourceOpen ? "source-open" : ""
      }`}
    >
      <div className="brand-strip">{brand}</div>
      <div className="workspace-panes">
        <aside className={`left-rail ${leftCollapsed ? "collapsed" : ""}`}>{left}</aside>
        <main className="chat-column">{center}</main>
        <aside className={`source-drawer ${sourceOpen ? "open" : ""}`}>{right}</aside>
      </div>
    </div>
  );
}
