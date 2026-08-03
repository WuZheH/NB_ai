# Search formal-entry first PDF preview root cause

## Candidate3 failure

Candidate3's independent packaged smoke rendered the PDF, canvas, page, and highlight layer. The first formal-entry run reached the same rendered state but remained `data-preview-ready=false` until the 60-second `first_preview` probe expired. The probe read the production semantic state; it did not use a canvas-, URL-, page-, or timer-based substitute.

## Why the 850ee preliminary fix was insufficient

Commit `850ee192f2fbfba97a51c26aba31bd9335e20339` treated a null return from `calculateHighlightScroll()` as a bounded degradation and completed the focus key. Reachability review disproved that explanation:

- a zero-sized container makes `focusToHighlightUnion()` return null before a pending focus is created;
- the scroll effect only calls `calculateHighlightScroll()` after `isRenderReadyForFocus()` has accepted positive page and rendered dimensions;
- a valid focus in a non-scrollable container returns the bounded target `{ left: 0, top: 0 }`, not null.

The `preview_focus_degraded` branch therefore could not repair the observed first-mount state. Its source-presence test also did not exercise React, layout, PDF rendering, or readiness behavior. The branch and that test were removed.

## Reproduced root cause

The production renderer was forced through the formal-entry ordering with a real PDF render while its preview scroller was `0 × 0`. The page, canvas, and exact overlay completed, but `focusToHighlightUnion()` could not create a focus, no pending focus existed, and `data-preview-ready` correctly stayed false. Releasing the layout did not cause the old component's effects to run again because `clientWidth` and `clientHeight` were imperative DOM reads rather than React-observable state. Candidate3 remained false and timed out with a fully rendered page and exact highlight.

## Final fix

`PdfLocationPreview` now observes its real scroll host with `ResizeObserver` and stores width and height in component state. A zero viewport is explicitly not semantically ready. When the viewport becomes positive, the size state re-runs fit and focus calculation, establishes pending focus, waits for the matching render scale, commits the bounded scroll target, and only then permits `data-preview-ready=true`. The observer disconnects on unmount, so Workspace and Evidence navigation remount cleanly. Selection keys still prevent a previous document's ready state from satisfying a new document.

No fixed delay, polling loop, test-only selector, canvas-only success condition, or PDF-error degradation was added. PDF load/render errors, invalid page/render identity, invalid overlay state, and zero viewport remain not-ready.

## Behavioral evidence

The regression suite now covers:

- non-scrollable containers returning `{ left: 0, top: 0 }`;
- missing focus or page/render dimensions returning null;
- `0 × 0` viewports rejecting focus and positive resized viewports producing focus;
- semantic rejection of load/render errors, page mismatch, unsettled overlay, restore, fit, and viewport states;
- a production Electron renderer that renders the PDF at `0 × 0`, observes the resize, re-runs focus, commits it, and reaches semantic ready;
- the same forced ordering against Candidate3, which times out with `status=ready`, exact overlay present, and `data-preview-ready=false`.
