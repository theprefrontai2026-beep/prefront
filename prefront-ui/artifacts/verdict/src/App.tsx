import SessionRunner from "./components/SessionRunner";
import { APPLICATIONS, appFromUrl, getApp, selectApp } from "./demo";

export default function App() {
  // The selected application comes from the URL, so a shared Verdict link
  // carries the application it was about. Previously this app was hardcoded to
  // one application in three places at once — the constant it imported, its own
  // tagline, and the orchestrator that constant named.
  const app = getApp(appFromUrl());

  return (
    <div className="verdict-shell">
      <header className="verdict-header">
        <div className="verdict-wordmark">Verdict</div>
        <div className="verdict-tagline">
          {/* What a run reports depends on where Prefront sits for this
              application. Claiming "out-of-band checks" over an in-band
              application with no tap is simply false. */}
          Business decision evaluator — run {app.label}'s scenario catalogue{" "}
          {app.outOfBand
            ? "against Prefront's out-of-band checks."
            : "through Prefront's governed runtime."}
        </div>
        {/* Rendered only when there is a choice to make. A switcher offering
            one option is noise, and a deployment with one application is the
            common case. */}
        {APPLICATIONS.length > 1 && (
          <div className="verdict-apps">
            {APPLICATIONS.map((a) => (
              <button
                key={a.id}
                type="button"
                className={`verdict-app${a.id === app.id ? " active" : ""}`}
                title={a.tagline}
                onClick={() => a.id !== app.id && selectApp(a.id)}
              >
                {a.label}
              </button>
            ))}
          </div>
        )}
      </header>
      {/* `key` remounts the runner when the application changes, so no
          catalogue, run result or open session can survive a switch. */}
      <SessionRunner key={app.id} app={app} />
    </div>
  );
}
