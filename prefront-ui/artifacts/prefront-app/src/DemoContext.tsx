/*
 * DemoContext — the app-level "which demo am I walking through" state.
 *
 * Persists the choice to localStorage (`prefront.demo`) so a reload keeps the
 * selected demo. `chooserOpen` starts true on first run (no choice yet) so the
 * initial switcher screen is shown before the app; the sidebar pill reopens it.
 */

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { DEMOS, DEFAULT_DEMO, getDemo, type DemoConfig, type DemoId } from "./demos";

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

function loadDemoId(): { id: DemoId; chosen: boolean } {
  try {
    const v = localStorage.getItem(DEMO_KEY);
    if (v && DEMOS.some((d) => d.id === v)) return { id: v as DemoId, chosen: true };
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
    setDemoId(id);
    setChosen(true);
    setChooserOpen(false);
    try { localStorage.setItem(DEMO_KEY, id); } catch { /* quota */ }
  }, []);

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
