"use client";

import { useCallback, useState } from "react";
import { ApiError, api, getErrorMessage, TaskStatus, UploadResult } from "@/lib/api";

type Phase = "idle" | "uploading" | "processing" | "done" | "error";

export default function UploadDropzone() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<UploadResult | null>(null);
  const [status, setStatus] = useState<TaskStatus | null>(null);

  const poll = useCallback(async (taskId: string, token: string) => {
    setPhase("processing");
    for (let i = 0; i < 60; i++) {
      try {
        const s = await api.getTask(taskId, token);
        setStatus(s);
        if (s.status === "done") {
          setPhase("done");
          window.location.href = `/result/${taskId}?token=${encodeURIComponent(token)}`;
          return;
        }
        if (s.status === "failed") {
          setPhase("error");
          setMessage(s.error_text || "处理失败");
          return;
        }
      } catch (e) {
        setPhase("error");
        setMessage(getErrorMessage(e));
        return;
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    setPhase("error");
    setMessage("处理超时，请稍后查询结果页。");
  }, []);

  const onFile = useCallback(
    async (file: File) => {
      setPhase("uploading");
      setMessage("");
      try {
        const res = await api.uploadResume(file);
        setResult(res);
        void poll(res.task_id, res.access_token);
      } catch (e) {
        setPhase("error");
        setMessage(e instanceof ApiError ? getErrorMessage(e) : "上传失败");
      }
    },
    [poll]
  );

  return (
    <div className="glass p-8">
      <label
        htmlFor="resume-file"
        className="flex cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed border-[var(--border)] p-10 text-center transition hover:border-brand focus-within:border-brand focus-within:ring-2 focus-within:ring-brand"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const f = e.dataTransfer.files?.[0];
          if (f) void onFile(f);
        }}
      >
        <span className="text-lg font-medium">拖拽简历到此处，或点击选择</span>
        <span className="text-sm opacity-60">支持 PDF / Word(.docx/.doc) / 纯文本 / Markdown（≤10MB）</span>
        <input
          id="resume-file"
          type="file"
          className="sr-only"
          accept=".pdf,.doc,.docx,.txt,.md"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void onFile(f);
          }}
        />
      </label>

      {phase === "uploading" && (
        <p className="mt-4 text-center" role="status" aria-live="polite">
          上传中…
        </p>
      )}
      {phase === "processing" && (
        <div className="mt-4 text-center" role="status" aria-live="polite">
          <p>处理中（{status?.progress ?? 0}%）…可离开，稍后凭结果链接查看。</p>
          {result && (
            <p className="mt-1 break-all text-xs opacity-60">
              结果链接：{result.result_url}
            </p>
          )}
        </div>
      )}
      {phase === "done" && (
        <p className="mt-4 text-center text-green-500" role="status" aria-live="polite">
          处理完成！（M1 将展示匹配岗位）
        </p>
      )}
      {phase === "error" && (
        <p className="mt-4 text-center text-red-500" role="alert" aria-live="assertive">
          {message}
        </p>
      )}
    </div>
  );
}
