import { createRoot } from "react-dom/client";
import App from "./App";
import { DemoProvider } from "./DemoContext";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <DemoProvider>
    <App />
  </DemoProvider>
);
