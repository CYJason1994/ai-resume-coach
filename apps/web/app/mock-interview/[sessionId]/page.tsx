"use client";

import { memo, useEffect, useRef, useState } from "react";
import {
  ApiError,
  api,
  ChatMessage,
  getErrorMessage,
  InterviewSession,
  SessionOverall,
} from "@/lib/api";

const DIM_LABELS: Record<string, string> = {
  behavioral: "行为面试",
  technical: "技术面试",
  role: "岗位匹配",
  stress: "压力面试",
  mixed: "综合",
};

function reportMarkdown(s: InterviewSession, o: SessionOverall): string {
  const lines: string[] = [];
  lines.push(`# 模拟面试评估报告 · ${s.job_title}`);
  lines.push("");
  lines.push(`> 维度聚焦：${DIM_LABELS[s.dimension_focus] ?? s.dimension_focus} · ${new Date().toLocaleDateString("zh-CN")}`);
  lines.push("");
  lines.push(`## 整体评分：${o.overall_score ?? 0} / 100`);
  if (o.summary) {
    lines.push("");
    lines.push(o.summary);
  }
  if (o.top_strengths?.length) {
    lines.push("");
    lines.push("### 亮点");
    o.top_strengths.forEach((x) => lines.push(`- ${x}`));
  }
  if (o.top_gaps?.length) {
    lines.push("");
    lines.push("### 待提升");
    o.top_gaps.forEach((x) => lines.push(`- ${x}`));
  }
  if (o.suggestion) {
    lines.push("");
    lines.push(`### 下一步建议\n${o.suggestion}`);
  }
  return lines.join("\n");
}

// P3：用 React.memo 包裹单条消息，避免每个 token 都重渲染整段对话列表
const MessageRow = memo(function MessageRow({
  m,
  isStreamingCursor,
}: {
  m: ChatMessage;
  isStreamingCursor: boolean;
}) {
  if (m.role === "assistant") {
    return (
      <div className="flex justify-start" role="group" aria-label="面试官">
        <div className="glass max-w-[85%] p-4">
          <p className="leading-relaxed whitespace-pre-wrap">
            {m.content || (isStreamingCursor ? "…" : "")}
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-end gap-1" role="group" aria-label="你">
      <div className="max-w-[85%] rounded-2xl bg-brand/15 p-4">
        <p className="leading-relaxed whitespace-pre-wrap">{m.content}</p>
      </div>
      {m.feedback && (
        <div className="glass w-full max-w-[85%] p-3 text-sm">
          <div className="flex items-center justify-between">
            <span className="font-medium text-brand">本轮评分</span>
            <span
              className={
                "rounded-full px-2.5 py-0.5 text-xs font-semibold " +
                (m.feedback.score >= 70
                  ? "bg-emerald-500/15 text-emerald-300"
                  : m.feedback.score >= 40
                    ? "bg-amber-500/15 text-amber-300"
                    : "bg-rose-500/15 text-rose-300")
              }
            >
              {m.feedback.score} / 100
            </span>
          </div>
          {m.feedback.strengths.length > 0 && (
            <p className="mt-2 text-emerald-300">
              亮点：{m.feedback.strengths.join("、")}
            </p>
          )}
          {m.feedback.improvements.length > 0 && (
            <p className="mt-1 text-rose-300">
              改进：{m.feedback.improvements.join("、")}
            </p>
          )}
        </div>
      )}
    </div>
  );
});

export default function MockInterviewPage({
  params,
}: {
  params: { sessionId: string };
}) {
  const { sessionId } = params;
  const [token, setToken] = useState("");
  const [session, setSession] = useState<InterviewSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const [overall, setOverall] = useState<SessionOverall | null>(null);
  const [finished, setFinished] = useState(false);
  const [copied, setCopied] = useState(false);
  // 仅用于屏幕阅读器的回合级状态播报（P2-13）：流式期间不逐 token 朗读，仅在回合边界更新
  const [liveStatus, setLiveStatus] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  // 记录上一条消息数量，区分「新增消息」（平滑滚动）与「流式内容增量」（即时滚动），避免逐 token smooth 造成卡顿
  const prevLenRef = useRef(0);

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("token") || "";
    setToken(t);
    if (!t) {
      setError("缺少访问令牌（URL 需带 ?token=）。");
      return;
    }
    api
      .getSession(sessionId, t)
      .then((s) => {
        setSession(s);
        setMessages(s.transcript || []);
        if (s.status === "finished" && s.overall_score) {
          setOverall({
            session_id: s.session_id,
            status: s.status,
            overall_score: s.overall_score.overall_score,
            summary: s.overall_score.summary,
            top_strengths: s.overall_score.top_strengths,
            top_gaps: s.overall_score.top_gaps,
            suggestion: s.overall_score.suggestion,
          });
          setFinished(true);
        }
      })
      .catch((e) =>
        setError(e instanceof ApiError ? getErrorMessage(e) : "加载会话失败")
      );
  }, [sessionId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const grew = messages.length > prevLenRef.current;
    prevLenRef.current = messages.length;
    // 尊重 prefers-reduced-motion：减少动画时一律用 'auto'，否则新消息用平滑滚动（P2-12）
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({ top: el.scrollHeight, behavior: reduce ? "auto" : grew ? "smooth" : "auto" });
  }, [messages]);

  const onSend = async () => {
    const text = input.trim();
    if (!text || streaming || !token || !session) return;
    setError("");
    const userMsg: ChatMessage = { role: "user", content: text, feedback: null };
    const assistantMsg: ChatMessage = { role: "assistant", content: "", feedback: null };
    setMessages((m) => [...m, userMsg, assistantMsg]);
    const userIdx = messages.length;
    const asstIdx = messages.length + 1;
    setInput("");
    setStreaming(true);
    // P2-13：流式开始即播报「正在输入」，避免逐 token 朗读
    setLiveStatus("面试官正在输入…");
    // 流异常/出错时清理悬挂的空助手气泡（P2-6）
    const cleanupEmptyAssistant = () =>
      setMessages((m) => {
        const last = m[m.length - 1];
        if (last && last.role === "assistant" && !last.content) return m.slice(0, -1);
        return m;
      });
    try {
      await api.sendSessionMessage(sessionId, token, text, (ev) => {
        if (ev.type === "token") {
          setMessages((m) => {
            const copy = [...m];
            copy[asstIdx] = { ...copy[asstIdx], content: copy[asstIdx].content + ev.value };
            return copy;
          });
        } else if (ev.type === "feedback") {
          setMessages((m) => {
            const copy = [...m];
            copy[userIdx] = { ...copy[userIdx], feedback: ev.value };
            return copy;
          });
        } else if (ev.type === "error") {
          setError(ev.value || "AI 面试官暂不可用，请稍后再试。");
          cleanupEmptyAssistant();
        }
      });
    } catch (e) {
      setError(e instanceof ApiError ? getErrorMessage(e) : (e as Error)?.message || "发送失败");
      cleanupEmptyAssistant();
    } finally {
      setStreaming(false);
      // P2-13：回合结束播报一次「已回复」
      setLiveStatus("面试官已回复");
    }
  };

  const onFinish = async () => {
    if (!token || finished) return;
    setError("");
    try {
      const o = await api.finishSession(sessionId, token);
      setOverall(o);
      setFinished(true);
    } catch (e) {
      setError(e instanceof ApiError ? getErrorMessage(e) : "评估失败");
    }
  };

  const onCopy = async () => {
    if (!session || !overall) return;
    await navigator.clipboard.writeText(reportMarkdown(session, overall));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const onDownload = () => {
    if (!session || !overall) return;
    const blob = new Blob([reportMarkdown(session, overall)], {
      type: "text/markdown;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `模拟面试评估_${session.job_title}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (error && !session) {
    return (
      <div className="glass p-8 text-center" role="alert" aria-live="assertive">
        <p className="text-rose-400">{error}</p>
        <a href="/" className="mt-4 inline-block text-sm text-brand underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          返回首页
        </a>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="glass p-8 text-center" role="status" aria-live="polite">
        <p className="text-lg font-medium">正在进入模拟面试…</p>
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-9rem)] flex-col gap-4">
      <section className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">
            <span className="gradient-text">模拟面试官</span>
          </h1>
          <p className="mt-1 text-sm opacity-70">
            {session.job_title} · 聚焦：{DIM_LABELS[session.dimension_focus] ?? session.dimension_focus}
          </p>
        </div>
        <div className="flex gap-2">
          {!finished && (
            <button
              onClick={onFinish}
              className="rounded-lg border border-amber-500/40 px-3 py-1.5 text-sm text-amber-300 transition hover:bg-amber-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
            >
              结束并评估
            </button>
          )}
          {finished && overall && (
            <>
              <button
                onClick={onCopy}
                className="rounded-lg border border-brand/40 px-3 py-1.5 text-sm text-brand transition hover:bg-brand/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
              >
                {copied ? "已复制 ✓" : "复制报告"}
              </button>
              <button
                onClick={onDownload}
                className="rounded-lg border border-brand/40 px-3 py-1.5 text-sm text-brand transition hover:bg-brand/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
              >
                下载 .md
              </button>
            </>
          )}
        </div>
      </section>

      {error && (
        <div className="glass border-rose-500/30 p-3 text-sm text-rose-300" role="alert" aria-live="assertive">
          {error}
        </div>
      )}

      {/* P2-13：屏幕阅读器回合级状态播报区（流式期间不朗读逐 token 内容） */}
      <div className="sr-only" role="status" aria-live="polite">
        {liveStatus}
      </div>

      {/* 对话区 */}
      <div
        ref={scrollRef}
        className="flex-1 space-y-4 overflow-y-auto pr-1"
        role="log"
        // P2-13：流式进行中关闭实时播报，避免每个 token 都重读整段对话；回合间隙恢复 polite
        aria-live={streaming ? "off" : "polite"}
        aria-relevant="additions"
        aria-label="面试对话记录"
      >
        {messages.length === 0 && (
          <p className="glass p-5 text-sm opacity-60">
            面试即将开始。介绍一下自己，或回答面试官的第一个问题吧。
          </p>
        )}
        {messages.map((m, i) => (
          <MessageRow
            key={i}
            m={m}
            isStreamingCursor={streaming && i === messages.length - 1}
          />
        ))}
      </div>

      {/* 输入区 */}
      {!finished ? (
        <div className="flex gap-2">
          <label htmlFor="answer-input" className="sr-only">
            你的回答
          </label>
          <textarea
            id="answer-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
            rows={2}
            maxLength={4000}
            aria-disabled={streaming || !input.trim()}
            placeholder="输入你的回答…（Enter 发送，Shift+Enter 换行）"
            className="flex-1 resize-none rounded-xl border border-white/10 bg-white/5 p-3 text-sm outline-none transition focus:border-brand/50 focus-visible:ring-2 focus-visible:ring-brand"
          />
          <button
            type="button"
            onClick={onSend}
            disabled={streaming || !input.trim()}
            aria-disabled={streaming || !input.trim()}
            className="magnetic-element self-end rounded-xl bg-brand/20 px-5 py-3 text-sm font-medium text-brand transition hover:bg-brand/30 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
          >
            {streaming ? "回答中…" : "发送"}
          </button>
        </div>
      ) : (
        overall && (
          <div className="glass space-y-3 p-5">
            <h2 className="text-lg font-semibold">
              整体评估：<span className="gradient-text">{overall.overall_score ?? 0} / 100</span>
            </h2>
            {overall.summary && <p className="text-sm opacity-80">{overall.summary}</p>}
            {overall.top_strengths?.length > 0 && (
              <p className="text-sm text-emerald-300">亮点：{overall.top_strengths.join("、")}</p>
            )}
            {overall.top_gaps?.length > 0 && (
              <p className="text-sm text-rose-300">待提升：{overall.top_gaps.join("、")}</p>
            )}
            {overall.suggestion && (
              <p className="text-sm opacity-80">建议：{overall.suggestion}</p>
            )}
          </div>
        )
      )}

      <div className="pt-1 text-center">
        <a href="/" className="text-sm text-brand underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          返回首页
        </a>
      </div>
    </div>
  );
}
