export function cleanSearchSnippet(text) {
  return String(text || "")
    .replace(/&lt;br\s*\/?&gt;/gi, " ")
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/(^|\n)\s{0,3}#{1,6}\s+/g, "$1")
    .replace(/\s#{1,6}\s+/g, " ")
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/\\(?:overset|stackrel)\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, (_match, top, base) => `${readableLatexToken(top)}${readableLatexToken(base)}`)
    .replace(/\\sum\s*((?:_\{[^{}]*\})?)\s*((?:\^\{[^{}]*\})?)/g, (_match, subscript, superscript) => `∑${subscript || ""}${superscript || ""}`)
    .replace(/\\prod\s*((?:_\{[^{}]*\})?)\s*((?:\^\{[^{}]*\})?)/g, (_match, subscript, superscript) => `∏${subscript || ""}${superscript || ""}`)
    .replace(/\\(?:left|right)\b/g, "")
    .replace(/\\(?:text|mathrm|mathbf|operatorname)\s*\{([^{}]*)\}/g, "$1")
    .replace(/\\(?:begin|end)\s*\{[^{}]*\}/g, " ")
    .replace(/\\([{}])/g, "$1")
    .replace(/\\([A-Za-z]+)(?=[^A-Za-z]|$)/g, (_match, command) => readableLatexCommand(command))
    .replace(/[$]+/g, " ")
    .replace(/(^|[\s，。,.；;:：])&(?=$|[\s，。,.；;:：])/g, "$1")
    .replace(/(^|\s)([-*+])\s+/g, "$1")
    .replace(/[ \t\r\n]+/g, " ")
    .trim();
}

function readableLatexCommand(command) {
  const symbols = {
    rho: "ρ",
    delta: "δ",
    Delta: "Δ",
    epsilon: "ε",
    theta: "θ",
    lambda: "λ",
    mu: "μ",
    pi: "π",
    phi: "φ",
    alpha: "α",
    beta: "β",
    gamma: "γ",
    leq: "≤",
    le: "≤",
    geq: "≥",
    ge: "≥",
    neq: "≠",
    ne: "≠",
    in: "∈",
    notin: "∉",
    subset: "⊂",
    subseteq: "⊆",
    to: "→",
    rightarrow: "→",
    leftarrow: "←",
    infty: "∞",
    cdot: "·",
    times: "×",
    ldots: "…",
    dots: "…",
    cdots: "…",
    triangle: "△",
    diamond: "◇",
    sim: "∼",
    min: "min",
    max: "max"
  };
  return symbols[command] || command;
}

function readableLatexToken(value) {
  return String(value || "")
    .replace(/\\([A-Za-z]+)(?=[^A-Za-z]|$)/g, (_match, command) => readableLatexCommand(command))
    .replace(/[{}$]+/g, "")
    .trim();
}

export function highlightQueryTerms(text, query) {
  const cleanedText = cleanSearchSnippet(text);
  if (!cleanedText) return [];
  const terms = searchHighlightTerms(query, cleanedText);
  if (!terms.length) return [{ text: cleanedText, highlighted: false }];
  const ranges = searchHighlightRanges(cleanedText, terms);
  if (!ranges.length) return [{ text: cleanedText, highlighted: false }];
  const segments = [];
  let cursor = 0;
  ranges.forEach((range) => {
    if (range.start > cursor) {
      segments.push({ text: cleanedText.slice(cursor, range.start), highlighted: false });
    }
    segments.push({ text: cleanedText.slice(range.start, range.end), highlighted: true });
    cursor = range.end;
  });
  if (cursor < cleanedText.length) {
    segments.push({ text: cleanedText.slice(cursor), highlighted: false });
  }
  return segments;
}

function searchHighlightTerms(query, text) {
  const source = cleanSearchSnippet(query);
  if (!source || !text) return [];
  const terms = new Set();
  const containsCjk = /[\u3400-\u9fff]/.test(source);
  if (containsCjk && source.length >= 2 && text.includes(source)) {
    terms.add(source);
  }
  if (containsCjk) {
    cjkFallbackTerms(source).forEach((term) => {
      if (text.includes(term)) terms.add(term);
    });
  }
  source
    .split(/[\s,;，；、/]+/)
    .map((term) => term.trim())
    .filter((term) => term.length >= 2)
    .forEach((term) => {
      if (/[\u3400-\u9fff]/.test(term)) {
        if (text.includes(term)) terms.add(term);
      } else if (term.length >= 2 && new RegExp(escapeSearchRegExp(term), "i").test(text)) {
        terms.add(term);
      }
    });
  return Array.from(terms).sort((left, right) => right.length - left.length);
}

function cjkFallbackTerms(source) {
  const terms = new Set();
  const cjkParts = source.match(/[\u3400-\u9fff]{2,}/g) || [];
  cjkParts.forEach((part) => {
    if (part.length >= 4) {
      for (let start = 0; start <= part.length - 2; start += 1) {
        for (let end = part.length; end >= start + 2; end -= 1) {
          const value = part.slice(start, end);
          if (value.length >= 2) terms.add(value);
        }
      }
    } else {
      terms.add(part);
    }
  });
  return Array.from(terms).sort((left, right) => right.length - left.length).slice(0, 8);
}

function searchHighlightRanges(text, terms) {
  const occupied = [];
  terms.forEach((term) => {
    const pattern = new RegExp(escapeSearchRegExp(term), "gi");
    let match = pattern.exec(text);
    while (match) {
      const start = match.index;
      const end = start + match[0].length;
      const ascii = /^[A-Za-z0-9]+$/.test(term);
      const boundaryOk = !ascii || (isSearchTokenBoundary(text[start - 1]) && isSearchTokenBoundary(text[end]));
      const overlaps = occupied.some((range) => start < range.end && end > range.start);
      if (boundaryOk && !overlaps) occupied.push({ start, end });
      match = pattern.exec(text);
    }
  });
  return occupied.sort((left, right) => left.start - right.start);
}

function isSearchTokenBoundary(char) {
  return !char || !/[A-Za-z0-9]/.test(char);
}

function escapeSearchRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
