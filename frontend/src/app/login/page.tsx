"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { api, tokenStore } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const path = mode === "login" ? "/auth/login" : "/auth/register";
      if (mode === "register") {
        await api(path, { method: "POST", body: { email, password } });
      }
      const res = await api<{ access_token: string }>("/auth/login", {
        method: "POST",
        body: { email, password },
      });
      tokenStore.set(res.access_token);
      tokenStore.setEmail(email);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-panel p-8">
        <div className="flex items-center gap-2 mb-6">
          <span className="text-accent text-2xl">⌁</span>
          <h1 className="text-lg font-semibold">FX Scalper Lab</h1>
        </div>
        <div className="flex gap-1 mb-6 text-sm">
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              onClick={() => {
                setMode(m);
                setError(null);
              }}
              className={`px-3 py-1.5 rounded-lg capitalize ${
                mode === m ? "bg-accent/15 text-accent" : "text-text-dim hover:text-text"
              }`}
            >
              {m}
            </button>
          ))}
        </div>
        <form onSubmit={submit} className="space-y-4">
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email"
            className="w-full rounded-lg border border-border bg-bg px-3 py-2.5 text-sm outline-none focus:border-accent"
          />
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password (min 8 chars)"
            className="w-full rounded-lg border border-border bg-bg px-3 py-2.5 text-sm outline-none focus:border-accent"
          />
          {error && <div className="text-xs text-danger">{error}</div>}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-accent/90 text-bg font-semibold py-2.5 text-sm hover:bg-accent disabled:opacity-50"
          >
            {busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
      </div>
    </div>
  );
}