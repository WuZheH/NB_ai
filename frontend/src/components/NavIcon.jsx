const ICONS = {
  readShelf: (
    <>
      <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5z" />
      <path d="M4 5.5v16" />
      <path d="M8 7h8" />
      <path d="M8 11h6" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4 4" />
    </>
  ),
  retrieval: (
    <>
      <path d="M4 5h10" />
      <path d="M4 10h7" />
      <path d="M4 15h6" />
      <circle cx="16" cy="15" r="4" />
      <path d="m19 18 2 2" />
    </>
  ),
  workspace: (
    <>
      <path d="M4 5h6v14H4z" />
      <path d="M10 5h10v14H10z" />
      <path d="M13 9h4" />
      <path d="M13 13h3" />
      <path d="M6.5 9h1" />
      <path d="M6.5 13h1" />
    </>
  ),
  research: (
    <>
      <path d="M8 9h8" />
      <path d="M8 13h5" />
      <path d="M20 11.5a7.5 7.5 0 0 1-11.8 6.1L4 18.5l1-3.9A7.5 7.5 0 1 1 20 11.5Z" />
    </>
  ),
  review: (
    <>
      <path d="M9 6h11" />
      <path d="M9 12h11" />
      <path d="M9 18h11" />
      <path d="m4 6 1 1 2-2" />
      <path d="m4 12 1 1 2-2" />
      <path d="m4 18 1 1 2-2" />
    </>
  ),
  importPreview: (
    <>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
      <path d="M14 3v5h5" />
      <path d="M12 17V11" />
      <path d="m9.5 13.5 2.5-2.5 2.5 2.5" />
    </>
  ),
  importReview: (
    <>
      <path d="M20 10.5V19a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9" />
      <path d="M8 8h5" />
      <path d="M8 13h4" />
      <path d="m15 6 2 2 4-4" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 0 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 0 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 0 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8 1.7 1.7 0 0 0 1.5 1h.2a2 2 0 0 1 0 4h-.2a1.7 1.7 0 0 0-1.4 1Z" />
    </>
  )
};

export default function NavIcon({ id }) {
  return (
    <svg className="navIconSvg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[id] || ICONS.readShelf}
    </svg>
  );
}
