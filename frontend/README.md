# NOTEBOOK_AI Frontend

Phase 17A introduces a minimal React + Vite workspace shell for the local-first NOTEBOOK_AI product.

## Commands

```powershell
npm install
npm run dev
npm run build
```

Dependencies are not vendored in the repository. Install them only after explicit approval.

## API Base URL

The frontend calls the local backend API at:

```text
http://127.0.0.1:8000
```

Override with:

```text
VITE_API_BASE_URL
```

## Boundary

The frontend is read-only in Phase 17A. It has no production write controls, no tag mutation controls, no autonomous workflow controls, and no external API integrations.
