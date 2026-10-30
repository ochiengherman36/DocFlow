import type { ExtractResult } from "@/lib/api";
import FlagList from "./flag-list";
import InvoiceEditor from "./invoice-editor";
import PdfPreview from "./pdf-preview";

export default function ReviewLayout({
  fileUrl,
  fileName,
  result,
}: {
  fileUrl: string;
  fileName: string;
  result: ExtractResult;
}) {
  return (
    <div className="mt-10 grid gap-8 lg:grid-cols-2">
      <PdfPreview url={fileUrl} name={fileName} />
      <div className="flex min-w-0 flex-col gap-8">
        <FlagList flags={result.flags} />
        <InvoiceEditor invoice={result.invoice} />
      </div>
    </div>
  );
}