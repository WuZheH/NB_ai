import LocalRetrievalPage from "./LocalRetrievalPage.jsx";

// Compatibility facade for historical imports. Routing and state ownership
// both live in LocalRetrievalPage; this file must never grow a second search UI.
export default function SearchPage() {
  return <LocalRetrievalPage />;
}
