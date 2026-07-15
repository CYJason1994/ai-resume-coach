"use client";

import { useEffect, useState } from "react";
import {
  getMe,
  getErrorMessage,
  login,
  logout,
  register,
  type UserView,
} from "@/lib/api";

type Mode = "login" | "register";

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [me, setMe] = useState<UserView | null>(null);

  useEffect(() => {
    getMe().then(setMe).catch(() => setMe(null));
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const user =
        mode === "login"
          ? await login(email, password)
          : await register(email, password);
      setMe(user);
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function onLogout() {
    await logout();
    setMe(null);
  }

  if (me) {
    return (
      <div className="glass mx-auto mt-10 max-w-md p-8 text-center">
        <h1 className="mb-2 text-xl font-bold">账户</h1>
        <p className="text-sm opacity-70">已登录</p>
        <p className="mt-2 text-xl font-semibold gradient-text">{me.email}</p>
        <button
          type="button"
          onClick={onLogout}
          className="mt-6 rounded-xl border border-[var(--border)] px-5 py-2 text-sm transition hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
        >
          退出登录
        </button>
      </div>
    );
  }

  return (
    <div className="glass mx-auto mt-10 max-w-md p-8">
      <h1 className="mb-6 text-center text-2xl font-bold">
        <span className="gradient-text">{mode === "login" ? "登录" : "注册"}</span>
      </h1>

      <div className="mb-6 flex gap-2 text-sm" role="group" aria-label="切换登录或注册">
        {(["login", "register"] as Mode[]).map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={mode === m}
            onClick={() => {
              setMode(m);
              setError(null);
            }}
            className={`flex-1 rounded-xl py-2 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${
              mode === m
                ? "gradient-text font-semibold"
                : "opacity-50"
            }`}
          >
            {m === "login" ? "登录" : "注册"}
          </button>
        ))}
      </div>

      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        <div>
          <label htmlFor="email" className="mb-1 block text-sm font-medium">
            邮箱
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            placeholder="邮箱"
            value={email}
            aria-invalid={!!error}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-xl border border-[var(--border)] bg-transparent px-4 py-3 outline-none focus:ring-2 focus:ring-indigo-400"
          />
        </div>
        <div>
          <label htmlFor="password" className="mb-1 block text-sm font-medium">
            密码
          </label>
          <input
            id="password"
            type="password"
            required
            minLength={mode === "register" ? 8 : 1}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            placeholder={mode === "register" ? "密码（至少 8 位）" : "密码"}
            value={password}
            aria-invalid={!!error}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-xl border border-[var(--border)] bg-transparent px-4 py-3 outline-none focus:ring-2 focus:ring-indigo-400"
          />
        </div>

        {error && (
          <p role="alert" aria-live="assertive" className="text-sm text-rose-500">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          aria-busy={busy}
          className="w-full rounded-xl bg-gradient-to-r from-indigo-500 via-violet-500 to-pink-500 py-3 font-semibold text-white transition hover:scale-[1.02] disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-brand"
        >
          {busy ? "处理中…" : mode === "login" ? "登录" : "注册并登录"}
        </button>
      </form>
    </div>
  );
}
