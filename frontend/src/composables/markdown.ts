import { marked } from "marked";
import DOMPurify from "dompurify";

marked.setOptions({ gfm: true, breaks: true });

/** Render assistant markdown, converting [#N] citation tokens into clickable spans. */
export function renderAnswer(md: string): string {
  // Replace [#1] / [#12] before markdown so they survive HTML rendering
  const withCites = md.replace(
    /\[#(\d+)\]/g,
    (_m, n) => `<span class="cite" data-cite="${n}">#${n}</span>`,
  );
  const html = marked.parse(withCites) as string;
  return DOMPurify.sanitize(html, {
    ADD_ATTR: ["data-cite"],
    ADD_TAGS: ["span"],
  });
}
