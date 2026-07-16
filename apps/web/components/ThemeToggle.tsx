"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark" | "system";

function apply(theme: Theme) {
  const isDark =
    theme === "dark" ||
    (theme === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", isDark);
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    const saved = (localStorage.getItem("theme") as Theme) || "system";
    setTheme(saved);
    apply(saved);
  }, []);

  // P2-14：选中「跟随系统」时，实时响应操作系统主题变化；切走时移除监听
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => apply("system");
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [theme]);

  function change(next: Theme) {
    setTheme(next);
    localStorage.setItem("theme", next);
    apply(next);
  }

  return (
    <div
      role="group"
      aria-label="主题切换"
      className="flex items-center gap-1 rounded-full border border-[var(--border)] p-1 text-sm"
    >
      {(["light", "dark", "system"] as Theme[]).map((t) => (
        <button
          key={t}
          type="button"
          onClick={() => change(t)}
          aria-pressed={theme === t}
          className={`rounded-full px-3 py-1 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${
            theme === t ? "bg-brand text-white" : "opacity-70 hover:opacity-100"
          }`}
        >
          {t === "light" ? "浅色" : t === "dark" ? "深色" : "跟随系统"}
        </button>
      ))}
    </div>
  );
}
