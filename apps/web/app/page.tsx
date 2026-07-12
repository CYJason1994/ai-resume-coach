import UploadDropzone from "@/components/UploadDropzone";

export default function Home() {
  return (
    <div className="space-y-10">
      <section className="text-center">
        <h1 className="text-4xl font-bold">
          <span className="gradient-text">简历优化平台</span>
        </h1>
        <p className="mt-3 opacity-70">
          上传简历 → 解析结构化 → 匹配岗位（O*NET 源，可配置）→ 面试题与模拟面试官（M2/M3）
        </p>
      </section>
      <UploadDropzone />
    </div>
  );
}
