"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { tokenStore } from "@/lib/api";
import Shell from "@/components/Shell";

export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const authed = tokenStore.get();

  useEffect(() => {
    if (!authed) router.replace("/login");
  }, [authed, router]);

  if (!authed) return null;
  return <Shell>{children}</Shell>;
}