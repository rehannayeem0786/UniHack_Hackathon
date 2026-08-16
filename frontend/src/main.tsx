import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "@/App";
// Fonts are bundled rather than fetched from a CDN, so the dashboard renders
// with its intended typography even with no internet — the demo scenario.
import "@fontsource-variable/inter";
import "@fontsource-variable/space-grotesk";
import "@/index.css";

const container = document.getElementById("root");
if (!container) throw new Error("Root element #root is missing from index.html");

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
