# Search formal-entry first PDF preview root cause

Candidate3's packaged smoke rendered the PDF, canvas, page, and highlight layer, but the formal-entry first preview could remain `data-preview-ready=false`. The ready contract waited for highlight auto-focus scrolling to return a scroll target. During the first formal mount the preview geometry can be valid while the scroll host is not yet scrollable; `calculateHighlightScroll()` then returns no target and the focus state never settled.

The fix records a bounded `preview_focus_degraded` stage and marks the focus contract complete when valid rendered overlay geometry exists but scrolling is unavailable. PDF load/page/render errors still remain not-ready, new documents reset state, and the stable probe continues to require `data-preview-ready=true`, `status=ready`, matching document/page, non-zero canvas dimensions, and the expected highlight strategy/count. No test-only bypass, fixed delay, URL-only check, or canvas-only check was added.

Regression coverage asserts the production component contains the degradation transition and the semantic-ready utility continues to reject loading, error, restore, overlay, and page-mismatch states.
