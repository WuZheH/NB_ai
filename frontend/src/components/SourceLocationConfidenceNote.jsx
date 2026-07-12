export default function SourceLocationConfidenceNote({ source = {}, location }) {
  const pdfPage = source?.pdf_page || source?.pdfPage;
  if (!pdfPage) return null;
  if (!location) {
    return <span className="pdfPageHint">来源页码：p.{pdfPage} · PDF 定位：未核查</span>;
  }
  if (location.status === "located") {
    return <span className="pdfPageHint">来源页码：p.{pdfPage} · PDF 定位：已定位</span>;
  }
  return <span className="pdfPageHint">来源页码：p.{pdfPage} · PDF 定位：未确认</span>;
}
