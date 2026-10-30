import UploadForm from "./upload-form";

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-16">
      <h1 className="text-3xl font-semibold tracking-tight">DocFlow</h1>
      <p className="mt-2 text-zinc-600 dark:text-zinc-400">
        Upload a supplier invoice PDF, digital or scanned, and review the data
        extracted from it.
      </p>
      <UploadForm />
    </main>
  );
}