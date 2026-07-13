// 前端 API 客户端：相对路径 /api（由 next.config 重写代理到后端，避免 CORS 预检）。
// 所有读路径自动携带 X-Access-Token（匿名访问控制契约，R2-C2）。

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    super((body as any)?.detail || `API error ${status}`);
    this.status = status;
    this.body = body;
  }
}

export function getErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    switch (e.status) {
      case 401:
      case 403:
        return "无权限访问：令牌无效或缺失。";
      case 404:
        return "未找到该任务。";
      case 422:
        return "上传校验失败，请检查文件类型与大小。";
      case 429:
        return "请求过于频繁，请稍后再试。";
      case 503:
        return "AI 服务暂不可用，已切换规则匹配。";
      default:
        return "服务器开小差了，请稍后重试。";
    }
  }
  return "发生未知错误。";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, body);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export interface UploadResult {
  task_id: string;
  access_token: string;
  result_url: string;
  status: string;
}

export interface TaskStatus {
  task_id: string;
  type: string;
  status: string;
  progress: number;
  error_text: string | null;
  updated_at: string;
}

export const api = {
  async uploadResume(file: File): Promise<UploadResult> {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/upload", { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new ApiError(res.status, body);
    }
    return res.json();
  },
  async getTask(taskId: string, token: string): Promise<TaskStatus> {
    return request<TaskStatus>(`/api/tasks/${taskId}?token=${encodeURIComponent(token)}`, {
      headers: { "X-Access-Token": token },
    });
  },
  async getResult(taskId: string, token: string): Promise<ResumeResult> {
    return request<ResumeResult>(
      `/api/tasks/${taskId}/result?token=${encodeURIComponent(token)}`,
      { headers: { "X-Access-Token": token } }
    );
  },
  async deleteResume(resumeId: string, token: string): Promise<void> {
    return request<void>(`/api/resumes/${resumeId}?token=${encodeURIComponent(token)}`, {
      method: "DELETE",
      headers: { "X-Access-Token": token },
    });
  },
  async generateInterview(
    resumeId: string,
    jobId: string,
    token: string
  ): Promise<InterviewList> {
    return request<InterviewList>("/api/interviews/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Access-Token": token },
      body: JSON.stringify({ resume_id: resumeId, job_id: jobId }),
    });
  },
  async getInterview(taskId: string, token: string): Promise<InterviewList> {
    return request<InterviewList>(
      `/api/interviews/${taskId}?token=${encodeURIComponent(token)}`,
      { headers: { "X-Access-Token": token } }
    );
  },
  // ── 模拟面试官（M3）──
  async createSession(
    req: {
      resume_id: string;
      job_id: string;
      interview_task_id?: string;
      dimension_focus?: string;
      mode?: string;
    },
    token: string
  ): Promise<InterviewSession> {
    return request<InterviewSession>("/api/interview-sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Access-Token": token },
      body: JSON.stringify(req),
    });
  },
  async getSession(sessionId: string, token: string): Promise<InterviewSession> {
    return request<InterviewSession>(
      `/api/interview-sessions/${sessionId}?token=${encodeURIComponent(token)}`,
      { headers: { "X-Access-Token": token } }
    );
  },
  async finishSession(sessionId: string, token: string): Promise<SessionOverall> {
    return request<SessionOverall>(`/api/interview-sessions/${sessionId}/finish`, {
      method: "POST",
      headers: { "X-Access-Token": token },
    });
  },
  // SSE 流式发送消息；onEvent 逐事件回调（{type:'token'|'feedback'|'done'|'error', value}）
  async sendSessionMessage(
    sessionId: string,
    token: string,
    message: string,
    onEvent: (ev: { type: string; value: any }) => void
  ): Promise<void> {
    const res = await fetch(`/api/interview-sessions/${sessionId}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Access-Token": token },
      body: JSON.stringify({ message }),
    });
    if (!res.ok || !res.body) {
      const body = await res.json().catch(() => null);
      throw new ApiError(res.status, body);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = frame.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const payload = line.slice(5).trim();
        try {
          onEvent(JSON.parse(payload));
        } catch {
          /* 忽略无法解析的帧 */
        }
      }
    }
  },
};

// ── 结果与结构化类型（M1）──
export interface ResumeStructured {
  name: string | null;
  title: string | null;
  summary: string | null;
  skills: string[];
  experience_years: number | null;
  education: string[];
  work_history: string[];
  projects: string[];
  languages: string[];
  location: string | null;
}
export interface MatchItem {
  job_id: string;
  title: string | null;
  title_zh: string | null;
  category: string | null;
  score: number;
  matched_skills: string[];
  missing_skills: string[];
  rationale: string | null;
}
export interface ResumeResult {
  task_id: string;
  resume_id: string;
  status: string;
  structured: ResumeStructured | null;
  matches: MatchItem[];
}

// ── 面试题目生成（M2）──
export interface InterviewQuestion {
  id: string;
  dimension: string;
  question: string;
  expected_focus: string | null;
  difficulty: string | null;
  order_index: number;
}
export interface InterviewList {
  task_id: string;
  resume_id: string;
  job_id: string;
  job_title: string;
  status: string;
  degraded: boolean;
  error_text: string | null;
  questions: InterviewQuestion[];
}

// ── 模拟面试官（M3）──
export interface FeedbackItem {
  score: number;
  strengths: string[];
  improvements: string[];
  dimension: string;
}
export interface ChatMessage {
  role: string;
  content: string;
  feedback: FeedbackItem | null;
}
export interface InterviewSession {
  session_id: string;
  resume_id: string;
  job_id: string;
  job_title: string;
  dimension_focus: string;
  status: string;
  transcript: ChatMessage[];
  overall_score: {
    overall_score: number;
    summary: string;
    top_strengths: string[];
    top_gaps: string[];
    suggestion: string;
  } | null;
}
export interface SessionOverall {
  session_id: string;
  status: string;
  overall_score: number | null;
  summary: string | null;
  top_strengths: string[];
  top_gaps: string[];
  suggestion: string | null;
}
