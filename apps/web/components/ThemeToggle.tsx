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

  function change(next: Theme) {
    setTheme(next);
    localStorage.setItem("theme", next);
    apply(next);
  }

  return (
    <div className="flex items-center gap-1 rounded-full border border-[var(--border)] p-1 text-sm">
      {(["light", "dark", "system"] as Theme[]).map((t) => (
        <button
          key={t}
          onClick={() => change(t)}
          className={`rounded-full px-3 py-1 transition ${
            theme === t ? "bg-brand text-white" : "opacity-70 hover:opacity-100"
          }`}
        >
          {t === "light" ? "浅色" : t === "dark" ? "深色" : "跟随系统"}
        </button>
      ))}
    </div>
  );
}
