import type { Metadata } from "next";
import ThemeToggle from "@/components/ThemeToggle";
import "./globals.css";

export const metadata: Metadata = {
  title: "ai-resume-coach · 简历优化平台",
  description: "多格式简历上传 → 解析 → 岗位匹配（生产级）",
};

// 在首次绘制前应用主题，避免闪烁（FOUC）
const themeScript = `
(function(){try{var t=localStorage.getItem('theme')||'system';
var d=t==='dark'||(t==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);
document.documentElement.classList.toggle('dark',d);}catch(e){}})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <header className="flex items-center justify-between p-5">
          <span className="text-lg font-semibold gradient-text">ai-resume-coach</span>
          <ThemeToggle />
        </header>
        <main className="mx-auto max-w-3xl px-5 py-10">{children}</main>
      </body>
    </html>
  );
}
