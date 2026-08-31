import { useState } from "react";
import { absUrl, currentLoc, buildHref } from "../lib/router";
import { withDemo } from "../routes";
import { useDemo } from "../DemoContext";

/**
 * "Copy link" — the share affordance for one artifact.
 *
 * `href` is app-relative (from routes.ts's builders); omit it to share the
 * page you are on. ?demo= is ALWAYS injected, whether or not the current URL
 * carries it: every /api/* call and every localStorage cache is demo-scoped,
 * so a link without it renders different content for the recipient.
 */
export default function CopyLink({ href, label, title = "Copy a shareable link", compact = true }: {
  href?: string;
  label?: string;
  title?: string;
  compact?: boolean;
}) {
  const { demoId } = useDemo();
  const [copied, setCopied] = useState(false);

  const target = () => {
    const loc = currentLoc();
    const rel = href ?? buildHref(loc.path, {}, loc.query);
    return absUrl(withDemo(rel, demoId));
  };

  const onClick = async (e: React.MouseEvent) => {
    // Rows, cards and graph nodes are themselves clickable — copying must not
    // also navigate.
    e.stopPropagation();
    e.preventDefault();
    const url = target();
    try {
      // navigator.clipboard is UNDEFINED on a non-secure origin, and this app
      // is served over plain HTTP on :5173 — so the fallback is load-bearing,
      // not polish (PolicyStudio's "Copy log" silently does nothing there).
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(url);
      else legacyCopy(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      window.prompt("Copy this link", url);
    }
  };

  return (
    <button
      type="button"
      className={`pf-copylink ${compact ? "compact" : ""} ${copied ? "copied" : ""}`}
      title={title}
      aria-label={title}
      onClick={onClick}
    >
      {copied ? <IconCheck /> : <IconLink />}
      {label ? <span>{copied ? "Copied" : label}</span> : null}
    </button>
  );
}

function legacyCopy(text: string) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } finally { document.body.removeChild(ta); }
}

function IconLink() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
    </svg>
  );
}

function IconCheck() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M20 6 9 17l-5-5"/>
    </svg>
  );
}
