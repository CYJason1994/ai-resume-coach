# 无障碍 / 60fps 加固自查清单（M4 W5）

范围：`apps/web`（Next.js 14 App Router）。不涉及 `apps/api`。

构建命令（在 `apps/web` 下，使用 managed node 22.22.2）：
```bash
export PATH="C:/Users/admin/.workbuddy/binaries/node/versions/22.22.2:$PATH"
./node_modules/.bin/next build
```

---

## 一、WCAG 2.1 AA 清单（逐项落实）

| # | WCAG 准则 | 要求 | 落实动作 | 文件 |
|---|-----------|------|----------|------|
| 1 | 1.3.1 信息结构 | 语义结构 / 标题层级有序 | 每个页面经 `app/layout.tsx` 的 `<main>` 渲染；`<header>` 为 banner；页面用 `<section>`，标题 `h1→h2→h3` 不跳级（home/result/interview/mock 均校验） | `app/layout.tsx`、`app/page.tsx`、`app/result/[taskId]/page.tsx`、`app/interview/[taskId]/page.tsx`、`app/mock-interview/[sessionId]/page.tsx` |
| 2 | 1.3.1 / 2.4.6 | 区段有可访问名称 | 首页 `<section aria-labelledby="home-heading">` 关联 `<h1 id="home-heading">` | `app/page.tsx` |
| 3 | 3.3.2 / 4.1.2 | 表单可访问 | login 邮箱/密码均加 `<label htmlFor>`，`aria-invalid` 关联错误态 | `app/login/page.tsx` |
| 4 | 4.1.3 / 3.3.1 | 错误可见且可读 | login 错误用 `role="alert" aria-live="assertive"`；result/interview/mock 错误同 | `app/login/page.tsx`、`app/result/[taskId]/page.tsx`、`app/interview/[taskId]/page.tsx`、`app/mock-interview/[sessionId]/page.tsx` |
| 5 | 2.4.7 | 可见焦点 | 全局 `:where(a,button,input,textarea,select,[tabindex]):focus-visible { outline: 2px solid brand }`；关键按钮/输入补 `focus-visible:ring-2 focus-visible:ring-brand` | `app/globals.css`、`components/ThemeToggle.tsx`、`components/UploadDropzone.tsx`、`app/login/page.tsx` 等 |
| 6 | 2.1.1 | 键盘可达 | 上传文件 input 由 `hidden` 改为 `sr-only`（保留 Tab 可达），label `focus-within:ring-2` 提供焦点指示 | `components/UploadDropzone.tsx` |
| 7 | 4.1.2 | 状态暴露 | 主题切换按钮 `aria-pressed`；收藏按钮 `aria-pressed`；login 模式按钮 `aria-pressed`；提交按钮 `aria-busy` | `components/ThemeToggle.tsx`、`app/login/page.tsx`、`app/interview/[taskId]/page.tsx` |
| 8 | 4.1.2 | 分组命名 | 主题切换容器 `role="group" aria-label="主题切换"`；login 模式切换 `role="group" aria-label="切换登录或注册"` | `components/ThemeToggle.tsx`、`app/login/page.tsx` |
| 9 | 4.1.2 / 2.1.1 | 禁用态语义 | 面试发送按钮 `disabled` + `aria-disabled` 双保险 | `app/mock-interview/[sessionId]/page.tsx` |
| 10 | 4.1.3 | 动态内容播报 | 各页 loading 用 `role="status" aria-live="polite"`；面试流式对话区 `role="log" aria-live="polite" aria-relevant="additions text" aria-label` | `components/UploadDropzone.tsx`、`app/result/[taskId]/page.tsx`、`app/interview/[taskId]/page.tsx`、`app/mock-interview/[sessionId]/page.tsx` |
| 11 | 1.4.11 | 非文本对比 | 沿用现有 token（`--fg`/`--bg`/`brand`），未引入低对比配色；错误用 `rose-300/400`、成功用 `emerald-300/400` 在深色玻璃上对比达标 | `app/globals.css` |
| 12 | 1.1.1 / 4.1.2 | 名称 / 装饰 | 纯装饰元素（gradient 文本包裹的 `<span>`）无需 aria；功能性按钮均有可见文本或 `aria-label`，无纯图标按钮缺失名称 | 全局 |
| 13 | 2.1.1 | 交互元素可操作 | 所有 `<button>` 加 `type`（默认 button/submit）；textarea 支持 Enter 发送 / Shift+Enter 换行（键盘可达） | `app/login/page.tsx`、`app/mock-interview/[sessionId]/page.tsx` |

> 注：项目中无自定义模态/抽屉组件，故未实现焦点陷阱；破坏性操作（删除简历）复用原生 `confirm()`，由浏览器托管焦点，自动满足焦点管理。

---

## 二、60fps / 性能

| 项 | 落实动作 | 文件 |
|----|----------|------|
| 动画仅用 transform/opacity | 既有交互用 `hover:scale-*`（transform）与颜色过渡；无 `width/height/top/left` 动画 | 全局 |
| 流式滚动不卡顿 | 面试页滚动效果改为：仅在「新增消息」时 `behavior: smooth`，流式内容增量（同一消息 token 追加）用 `behavior: auto`，避免逐 token 排队 smooth 动画 | `app/mock-interview/[sessionId]/page.tsx` |
| 重排规避 | 流式追加仅更新 React state 中对应消息 `content`，尾部滚动仅改 `scrollTop`（合成层友好），不触发 layout/paint 重排 | `app/mock-interview/[sessionId]/page.tsx` |
| `prefers-reduced-motion` | 全局兜底：关闭 animation/transition/scroll-behavior | `app/globals.css` |
| `will-change` 谨慎 | 未盲目加 `will-change`；仅依赖 transform/opacity 与 `scrollRef` 直接滚动 | — |

---

## 三、构建验证

- `next build`（类型检查 + 构建）零错误通过。详见交付回复中的构建结果。
