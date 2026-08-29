import Link from "next/link";
import { usePathname } from "next/navigation";
import { tokenStore } from "@/lib/api";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: "▦" },
  { href: "/strategies", label: "Strategies", icon: "◈" },
  { href: "/backtests", label: "Backtest Lab", icon: "◉" },
  { href: "/paper", label: "Paper Trading", icon: "≋" },
  { href: "/risk", label: "Risk Center", icon: "⛨" },
  { href: "/settings", label: "Settings", icon: "⚙" },
];

export default function Sidebar() {
  const pathname = usePathname();
  const email = tokenStore.getEmail();

  return (
    <aside className="w-56 shrink-0 border-r border-border bg-panel flex flex-col">
      <div className="px-4 py-4">
        <Link href="/dashboard" className="flex items-center gap-2">
          <span className="text-accent text-xl">⌁</span>
          <span className="font-semibold tracking-tight">FX Scalper Lab</span>
        </Link>
      </div>
      <nav className="flex-1 px-2 space-y-1 text-sm">
        {NAV.map((item) => {
          const active =
            pathname === item.href || pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 rounded-lg px-3 py-2 transition-colors ${
                active
                  ? "bg-accent/10 text-accent"
                  : "text-text-dim hover:bg-panel2 hover:text-text"
              }`}
            >
              <span className="w-4 text-center">{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>
      {email && (
        <div className="px-4 py-3 border-t border-border text-xs text-text-dim truncate">
          {email}
        </div>
      )}
    </aside>
  );
}