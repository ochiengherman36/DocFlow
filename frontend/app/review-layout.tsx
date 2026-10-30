import type { ExtractResult } from "@/lib/api";
import InvoiceEditor from "./invoice-editor";
import PdfPreview from "./pdf-preview";

export default function ReviewLayout({
  fileUrl,
  fileName,
  result,
  apiUrl,
}: {
  fileUrl: string;
  fileName: string;
  result: ExtractResult;
  apiUrl: string;
}) {
  return (
    <div className="mt-10 grid gap-8 lg:grid-cols-2">
      <PdfPreview url={fileUrl} name={fileName} />
      <div className="flex min-w-0 flex-col gap-8">
        <InvoiceEditor invoice={result.invoice} initialFlags={result.flags} apiUrl={apiUrl} />
      </div>
    </div>
  );
}