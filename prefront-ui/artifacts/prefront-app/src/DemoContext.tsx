/*
 * DemoContext — the app-level "which demo am I walking through" state.
 *
 * Persists the choice to localStorage (`prefront.demo`) so a reload keeps the
 * selected demo, and mirrors it into the URL as `?demo=` so a shared link
 * reproduces the sender's demo (every /api/* call and localStorage cache is
 * demo-scoped). `chooserOpen` starts true on first run (no choice yet) so the
 * initial switcher screen is shown before the app; the sidebar pill reopens it.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { DEMOS, DEFAULT_DEMO, getDemo, type DemoConfig, type DemoId } from "./demos";
import { currentLoc, navigate, setParams, useLoc } from "./lib/router";
import { TAB_PATH, tabFromPath } from "./routes";

const DEMO_KEY = "prefront.demo";

interface DemoContextValue {
  demo: DemoConfig;              // active demo config
  demoId: DemoId;
  demos: DemoConfig[];           // the full registry (for the chooser)
  chooserOpen: boolean;          // is the initial/switch chooser showing?
  chosen: boolean;               // has the user ever made a choice?
  openChooser: () => void;
  selectDemo: (id: DemoId) => void;
}

const DemoCtx = createContext<DemoContextValue | null>(null);

const isDemoId = (v: string | null): v is DemoId => !!v && DEMOS.some((d) => d.id === v);

// ?demo= beats localStorage, and counts as a choice: without that a shared
// link would be swallowed by the first-run chooser overlay. Resolved
// synchronously in the useState initialiser below, so there is no flash.
function loadDemoId(): { id: DemoId; chosen: boolean } {
  const fromUrl = currentLoc().query.get("demo");
  if (isDemoId(fromUrl)) {
    try { localStorage.setItem(DEMO_KEY, fromUrl); } catch { /* quota */ }
    return { id: fromUrl, chosen: true };
  }
  try {
    const v = localStorage.getItem(DEMO_KEY);
    if (isDemoId(v)) return { id: v, chosen: true };
  } catch { /* ignore */ }
  return { id: DEFAULT_DEMO, chosen: false };
}

export function DemoProvider({ children }: { children: React.ReactNode }) {
  const initial = loadDemoId();
  const [demoId, setDemoId] = useState<DemoId>(initial.id);
  const [chosen, setChosen] = useState(initial.chosen);
  // Show the chooser up front until the user has explicitly picked a demo.
  const [chooserOpen, setChooserOpen] = useState(!initial.chosen);

  const selectDemo = useCallback((id: DemoId) => {
    // SWITCHING demos invalidates any artifact in the URL — a session/finding/
    // node id from one demo means nothing in the other — so the path is cut
    // back to the tab. Answering the first-run chooser is NOT a switch: the
    // user may have arrived on a shared deep link, and that link must survive.
    const switching = chosen && id !== demoId;
    setDemoId(id);
    setChosen(true);
    setChooserOpen(false);
    try { localStorage.setItem(DEMO_KEY, id); } catch { /* quota */ }
    if (switching) navigate(`${TAB_PATH[tabFromPath(currentLoc().segs)]}?demo=${id}`, { replace: true });
    else setParams({ demo: id }, { replace: true });
  }, [chosen, demoId]);

  // Back/forward across a link that carried a different ?demo=. Equality-
  // guarded, so this can never bounce against selectDemo's own write.
  const urlDemo = useLoc().query.get("demo");
  useEffect(() => {
    if (!isDemoId(urlDemo) || urlDemo === demoId) return;
    setDemoId(urlDemo);
    setChosen(true);
    setChooserOpen(false);
    try { localStorage.setItem(DEMO_KEY, urlDemo); } catch { /* quota */ }
  }, [urlDemo, demoId]);

  // A demo restored from localStorage isn't in the URL yet — put it there so
  // the address bar is copy-pasteable from the very first render.
  useEffect(() => {
    if (chosen && !urlDemo) setParams({ demo: demoId }, { replace: true });
  }, [chosen, urlDemo, demoId]);

  const openChooser = useCallback(() => setChooserOpen(true), []);

  const value = useMemo<DemoContextValue>(() => ({
    demo: getDemo(demoId),
    demoId,
    demos: DEMOS,
    chooserOpen,
    chosen,
    openChooser,
    selectDemo,
  }), [demoId, chooserOpen, chosen, openChooser, selectDemo]);

  return <DemoCtx.Provider value={value}>{children}</DemoCtx.Provider>;
}

export function useDemo(): DemoContextValue {
  const ctx = useContext(DemoCtx);
  if (!ctx) throw new Error("useDemo must be used within a DemoProvider");
  return ctx;
}
