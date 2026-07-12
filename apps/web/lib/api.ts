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
