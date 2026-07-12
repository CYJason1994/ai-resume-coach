"use client";

import { useEffect, useState } from "react";
import { ApiError, api, getErrorMessage, ResumeResult, TaskStatus } from "@/lib/api";

function SkillTags({ skills, tone }: { skills: string[]; tone: "ok" | "miss" }) {
  if (!skills.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {skills.map((s) => (
        <span
          key={s}
          className={
            "rounded-full px-2.5 py-0.5 text-xs " +
            (tone === "ok"
              ? "bg-emerald-500/15 text-emerald-300"
              : "bg-rose-500/15 text-rose-300")
          }
        >
          {s}
        </span>
      ))}
    </div>
  );
}

export default function ResultPage({ params }: { params: { taskId: string } }) {
  const { taskId } = params;
  const [token, setToken] = useState("");
  const [status, setStatus] = useState<TaskStatus | null>(null);
  const [result, setResult] = useState<ResumeResult | null>(null);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);

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
          const s = await api.getTask(taskId, t);
          setStatus(s);
          if (s.status === "done") {
            const r = await api.getResult(taskId, t);
            setResult(r);
            return;
          }
          if (s.status === "failed") {
            setError(s.error_text || "处理失败");
            return;
          }
        } catch (e) {
          setError(e instanceof ApiError ? getErrorMessage(e) : "查询失败");
          return;
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
      setError("处理超时，请稍后刷新查看。");
    };
    poll();
    return () => {
      active = false;
    };
  }, [taskId]);

  const onDelete = async () => {
    if (!result || !token) return;
    if (!confirm("确认删除这份简历？文件与可访问的分析结果将被移除，服务器上的原始数据将按要求清除。")) return;
    setDeleting(true);
    try {
      await api.deleteResume(result.resume_id, token);
      alert("简历已删除。");
      window.location.href = "/";
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setDeleting(false);
    }
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

  if (!result) {
    return (
      <div className="glass p-8 text-center">
        <p className="text-lg font-medium">正在分析你的简历…</p>
        <p className="mt-2 text-sm opacity-60">
          进度：{status?.progress ?? 0}% · 可在完成后刷新本页
        </p>
      </div>
    );
  }

  const s = result.structured;
  return (
    <div className="space-y-6">
      <section className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">
          <span className="gradient-text">分析结果</span>
        </h1>
        <button
          onClick={onDelete}
          disabled={deleting}
          className="rounded-lg border border-rose-500/40 px-3 py-1.5 text-sm text-rose-300 transition hover:bg-rose-500/10 disabled:opacity-50"
        >
          {deleting ? "删除中…" : "删除简历"}
        </button>
      </section>

      {/* 结构化预览 */}
      <section className="glass p-6">
        <h2 className="mb-3 text-lg font-semibold">简历画像</h2>
        {s?.title && (
          <p className="text-sm opacity-70">
            目标职位：<span className="opacity-100">{s.title}</span>
          </p>
        )}
        {s?.experience_years != null && (
          <p className="text-sm opacity-70">经验年限：{s.experience_years} 年</p>
        )}
        <div className="mt-3">
          <p className="mb-1 text-sm font-medium">技能</p>
          <SkillTags skills={s?.skills ?? []} tone="ok" />
        </div>
        {s?.summary && (
          <p className="mt-3 text-sm opacity-70">摘要：{s.summary}</p>
        )}
        {s?.education?.length ? (
          <p className="mt-3 text-sm opacity-70">教育：{s.education.join("、")}</p>
        ) : null}
      </section>

      {/* 匹配岗位 */}
      <section>
        <h2 className="mb-3 text-lg font-semibold">匹配岗位（Top {result.matches.length}）</h2>
        <div className="space-y-4">
          {result.matches.map((m) => (
            <article key={m.job_id} className="glass p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="font-semibold">{m.title_zh || m.title}</h3>
                  {m.category && (
                    <span className="text-xs opacity-50">{m.category}</span>
                  )}
                </div>
                <div className="text-right">
                  <span className="text-xl font-bold text-emerald-400">
                    {Math.round(m.score)}%
                  </span>
                  <p className="text-xs opacity-50">匹配度</p>
                </div>
              </div>
              {m.rationale && (
                <p className="mt-2 text-sm opacity-70">{m.rationale}</p>
              )}
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <div>
                  <p className="mb-1 text-xs font-medium text-emerald-300">已具备</p>
                  <SkillTags skills={m.matched_skills} tone="ok" />
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium text-rose-300">建议补充</p>
                  <SkillTags skills={m.missing_skills} tone="miss" />
                </div>
              </div>
            </article>
          ))}
          {result.matches.length === 0 && (
            <p className="glass p-5 text-sm opacity-60">
              暂未匹配到岗位（可能岗位库尚未摄入，或简历内容过短）。
            </p>
          )}
        </div>
      </section>
    </div>
  );
}
