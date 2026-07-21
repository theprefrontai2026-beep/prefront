/*
 * DemoChooser — the initial switcher. A full-screen overlay that lets the user
 * pick which bundled demo (SecureBank, LoanPro, …) to walk through. Shown on
 * first run (no choice persisted yet) and reopened from the sidebar demo pill.
 */

import { useDemo } from "../DemoContext";
import type { DemoConfig } from "../demos";

export default function DemoChooser() {
  const { demos, demoId, chosen, chooserOpen, selectDemo } = useDemo();
  if (!chooserOpen) return null;

  return (
    <div className="pf-demo-chooser">
      <div className="pf-demo-chooser-inner">
        <div className="pf-demo-chooser-head">
          <span className="pf-logo-wordmark" aria-hidden>
            <span className="pf-logo-p">p</span><span className="pf-logo-f">f</span>
          </span>
          <h1>Choose a demo</h1>
          <p>
            Prefront is a governed data-access runtime — the same engine, any domain.
            Pick a worked example to walk through; you can switch anytime from the sidebar.
          </p>
        </div>

        <div className="pf-demo-cards">
          {demos.map((d) => (
            <DemoCard
              key={d.id}
              demo={d}
              active={chosen && d.id === demoId}
              onPick={() => selectDemo(d.id)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function DemoCard({ demo, active, onPick }: { demo: DemoConfig; active: boolean; onPick: () => void }) {
  return (
    <button
      className={`pf-demo-card ${active ? "active" : ""}`}
      style={{ ["--demo-accent" as any]: demo.accent }}
      onClick={onPick}
    >
      <div className="pf-demo-card-glyph" aria-hidden>{demo.glyph}</div>
      <div className="pf-demo-card-label">{demo.label}</div>
      <div className="pf-demo-card-tag">{demo.tagline}</div>
      <div className="pf-demo-card-blurb">{demo.blurb}</div>
      <div className="pf-demo-card-meta">
        <span className="pf-demo-card-count">{demo.scenarioCount} scenarios</span>
        {active && <span className="pf-demo-card-current">current</span>}
      </div>
    </button>
  );
}
