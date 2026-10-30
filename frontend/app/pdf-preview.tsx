export default function PdfPreview({ url, name }: { url: string; name: string }) {
  return (
    <div className="flex flex-col gap-2 lg:sticky lg:top-6 lg:self-start">
      <h2 className="text-xl font-semibold">Original document</h2>
      <iframe
        src={url}
        title={`Preview of ${name}`}
        className="h-[60vh] w-full rounded border border-zinc-300 bg-white lg:h-[80vh] dark:border-zinc-700"
      />
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-sm text-zinc-600 underline dark:text-zinc-400"
      >
        Open the PDF in a new tab
      </a>
    </div>
  );
}