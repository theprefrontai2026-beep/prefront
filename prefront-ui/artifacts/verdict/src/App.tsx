import SessionRunner from "./components/SessionRunner";
import { LOANPRO } from "./demo";

export default function App() {
  return (
    <div className="verdict-shell">
      <header className="verdict-header">
        <div className="verdict-wordmark">Verdict</div>
        <div className="verdict-tagline">Business decision evaluator — run LoanPro's scenario catalogue against Prefront's out-of-band checks.</div>
      </header>
      <SessionRunner demo={LOANPRO} />
    </div>
  );
}
