import { readFile } from "node:fs/promises";
import { join } from "node:path";

export async function loadSearchDesignTokens(designSystemRoot) {
  const source = await readFile(join(designSystemRoot, "tokens.css"), "utf8");
  return Object.freeze({
    primary: cssToken(source, "--search-brand"),
    background: cssToken(source, "--search-bg"),
  });
}

function cssToken(source, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(new RegExp(`${escaped}\\s*:\\s*(#[0-9a-fA-F]{6})\\s*;`));
  if (!match) throw new Error(`search_design_token_missing:${name}`);
  return match[1];
}
