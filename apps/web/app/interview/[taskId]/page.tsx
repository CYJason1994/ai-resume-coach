"use client";

import { useEffect, useMemo, useState } from "react";
import { ApiError, api, getErrorMessage, InterviewList } from "@/lib/api";

const DIM_LABELS: Record<string, string> = {
  behavioral: "行为面试",
  technical: "技术面试",
  role: "岗位匹配",
  stress: "压力面试",
};
const DIM_ORDER = ["behavioral", "technical", "role", "stress"];
const DIFF_LABELS: Record<string, string> = {
  junior: "初级",
  mid: "中级",
  senior: "高级",
};
const FAV_KEY = "arc_favorites";

type Fav = { task_id: string; token: string; job_title: string; ts: number };

function toMarkdown(d: InterviewList): string {
  const lines: string[] = [];
  lines.push(`# 面试题 · ${d.job_title}`);
  lines.push("");
  lines.push(
    `> 由 AI 简历教练生成${d.degraded ? "（规则模板兜底）" : ""} · ${new Date().toLocaleDateString("zh-CN")}`
  );
  lines.push("");
  for (const dim of DIM_ORDER) {
    const qs = d.questions.filter((q) => q.dimension === dim);
    if (!qs.length) continue;
    lines.push(`## ${DIM_LABELS[dim] ?? dim}`);
    lines.push("");
    qs.forEach((q, i) => {
      lines.push(`${i + 1}. ${q.question}`);
      const meta: string[] = [];
      if (q.expected_focus) meta.push(`考察点：${q.expected_focus}`);
      if (q.difficulty) meta.push(`难度：${DIFF_LABELS[q.difficulty] ?? q.difficulty}`);
      if (meta.length) lines.push(`   - ${meta.join(" · ")}`);
    });
    lines.push("");
  }
  return lines.join("\n");
}

export default function InterviewPage({ params }: { params: { taskId: string } }) {
  const { taskId } = params;
  const [token, setToken] = useState("");
  const [data, setData] = useState<InterviewList | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [favorited, setFavorited] = useState(false);

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("token") || "";
    setToken(t);
    if (!t) {
      setError("缺少访问令牌（URL 需带 ?token=）。");
      return;
    }
    let active = true;
    const poll = async () => {
      for (let i = 0; i < 90; i++) {
        if (!active) return;
        try {
          const d = await api.getInterview(taskId, t);
          setData(d);
          if (d.status === "done") {
            const favs: Fav[] = JSON.parse(localStorage.getItem(FAV_KEY) || "[]");
            setFavorited(favs.some((f) => f.task_id === d.task_id));
            return;
          }
          if (d.status === "failed") {
            setError(d.error_text || "面试题生成失败");
            return;
          }
        } catch (e) {
          setError(e instanceof ApiError ? getErrorMessage(e) : "查询失败");
          return;
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
      setError("生成超时，请稍后刷新查看。");
    };
    poll();
    return () => {
      active = false;
    };
  }, [taskId]);

  const grouped = useMemo(() => {
    if (!data) return [];
    return DIM_ORDER.map((dim) => ({
      dim,
      label: DIM_LABELS[dim] ?? dim,
      items: data.questions.filter((q) => q.dimension === dim),
    })).filter((g) => g.items.length > 0);
  }, [data]);

  const onCopy = async () => {
    if (!data) return;
    await navigator.clipboard.writeText(toMarkdown(data));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const onDownload = () => {
    if (!data) return;
    const blob = new Blob([toMarkdown(data)], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `面试题_${data.job_title}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const onFavorite = () => {
    if (!data) return;
    const favs: Fav[] = JSON.parse(localStorage.getItem(FAV_KEY) || "[]");
    if (favs.some((f) => f.task_id === data.task_id)) {
      setFavorited(true);
      return;
    }
    favs.push({ task_id: data.task_id, token, job_title: data.job_title, ts: Date.now() });
    localStorage.setItem(FAV_KEY, JSON.stringify(favs));
    setFavorited(true);
  };

  if (error) {
    return (
      <div className="glass p-8 text-center">
        <p className="text-rose-400">{error}</p>
        <a href="/" className="mt-4 inline-block text-sm text-brand underline">
          返回首页
        </a>
      </div>
    );
  }

  if (!data || data.status !== "done") {
    return (
      <div className="glass p-8 text-center">
        <p className="text-lg font-medium">正在为你生成针对性面试题…</p>
        <p className="mt-2 text-sm opacity-60">通常只需数秒，完成后本页自动刷新</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <section className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">
            <span className="gradient-text">面试题</span>
          </h1>
          <p className="mt-1 text-sm opacity-70">目标岗位：{data.job_title}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onCopy}
            className="rounded-lg border border-brand/40 px-3 py-1.5 text-sm text-brand transition hover:bg-brand/10"
          >
            {copied ? "已复制 ✓" : "复制 Markdown"}
          </button>
          <button
            onClick={onDownload}
            className="rounded-lg border border-brand/40 px-3 py-1.5 text-sm text-brand transition hover:bg-brand/10"
          >
            下载 .md
          </button>
          <button
            onClick={onFavorite}
            disabled={favorited}
            className="rounded-lg border border-emerald-500/40 px-3 py-1.5 text-sm text-emerald-300 transition hover:bg-emerald-500/10 disabled:opacity-50"
          >
            {favorited ? "已收藏 ★" : "收藏"}
          </button>
        </div>
      </section>

      {data.degraded && (
        <div className="glass border-amber-500/30 p-4 text-sm text-amber-300">
          {data.error_text || "AI 生成暂不可用，已使用规则模板兜底。"}
        </div>
      )}

      {grouped.map((g) => (
        <section key={g.dim}>
          <h2 className="mb-3 text-lg font-semibold">{g.label}</h2>
          <div className="space-y-3">
            {g.items.map((q, i) => (
              <article key={q.id} className="glass p-5">
                <div className="flex items-start gap-3">
                  <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand/20 text-xs font-semibold text-brand">
                    {i + 1}
                  </span>
                  <div className="flex-1">
                    <p className="leading-relaxed">{q.question}</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {q.expected_focus && (
                        <span className="rounded-full bg-sky-500/15 px-2.5 py-0.5 text-xs text-sky-300">
                          考察点：{q.expected_focus}
                        </span>
                      )}
                      {q.difficulty && (
                        <span className="rounded-full bg-violet-500/15 px-2.5 py-0.5 text-xs text-violet-300">
                          {DIFF_LABELS[q.difficulty] ?? q.difficulty}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>
      ))}

      <div className="pt-2 text-center">
        <a href="/" className="text-sm text-brand underline">
          返回首页
        </a>
      </div>
    </div>
  );
}
