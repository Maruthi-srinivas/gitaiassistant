import type { ReactNode } from "react";

type Props = {
  header: ReactNode;
  left: ReactNode;
  center: ReactNode;
  right: ReactNode;
  sourcePanel: ReactNode;
  leftCollapsed: boolean;
  sourceOpen: boolean;
};

export default function WorkspaceShell({
  header,
  left,
  center,
  right,
  sourcePanel,
  leftCollapsed,
  sourceOpen,
}: Props) {
  return (
    <div
      className={`workspace-shell ${leftCollapsed ? "left-collapsed" : ""} ${
        sourceOpen ? "source-open" : ""
      }`}
    >
      {header}
      <div className="workspace-panes">
        <aside className={`left-rail ${leftCollapsed ? "collapsed" : ""}`}>{left}</aside>
        <main className="chat-column">{center}</main>
        <aside className="right-column">
          {sourceOpen ? (
            <div className="source-drawer open">{sourcePanel}</div>
          ) : (
            right
          )}
        </aside>
      </div>
    </div>
  );
}
