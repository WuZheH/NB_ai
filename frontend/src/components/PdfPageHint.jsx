import { isLocalPdfFallback } from "../utils/formatters.js";

export default function PdfPageHint({ source = {} }) {
  const pdfPage = source?.pdf_page || source?.pdfPage;
  if (!pdfPage || !isLocalPdfFallback(source)) return null;
  return <span className="pdfPageHint">页码提示：第 {pdfPage} 页 · {source.zotero_binding_status || "Zotero 未绑定"}</span>;
}
