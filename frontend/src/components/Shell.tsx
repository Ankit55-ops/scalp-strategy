import { ReactNode } from "react";
import Sidebar from "./Sidebar";
import LogoutButton from "./LogoutButton";

export default function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-bg text-text">
      <Sidebar />
      <main className="flex-1 flex flex-col">
        <header className="flex items-center justify-between px-6 py-3 border-b border-border bg-panel/60">
          <div className="text-xs text-text-dim">FX Scalper Lab · research & paper trading</div>
          <LogoutButton />
        </header>
        <div className="flex-1 p-6 overflow-y-auto">{children}</div>
      </main>
    </div>
  );
}