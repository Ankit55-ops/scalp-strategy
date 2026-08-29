"use client";

import { tokenStore } from "@/lib/api";
import { useRouter } from "next/navigation";

export default function LogoutButton() {
  const router = useRouter();
  return (
    <button
      onClick={() => {
        tokenStore.clear();
        router.push("/login");
      }}
      className="text-xs px-3 py-1.5 rounded-lg border border-border text-text-dim hover:text-text hover:border-accent transition-colors"
    >
      Sign out
    </button>
  );
}