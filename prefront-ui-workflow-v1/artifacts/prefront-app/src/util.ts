export function localTime(ts: string | null | undefined): string {
  if (!ts) return "";
  let s = String(ts).trim();
  if (!/[zZ]|[+-]\d\d:?\d\d$/.test(s)) s = s.replace(" ", "T") + "Z";
  const d = new Date(s);
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
}

export function parseKV(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of (text || "").split("\n")) {
    const i = line.indexOf("=");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}
