import React from "react";
import ReactDOM from "react-dom/client";
import { TooltipProvider } from "@appica/ui-react/tooltip";
import { ReducedMotionProvider } from "@appica/ui-react/providers/reduced-motion-provider";
import { ThemeProvider } from "@appica/ui-react/providers/theme-provider";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

const desktopMode = new URLSearchParams(window.location.search).get("app") === "1";
document.documentElement.classList.toggle("desktop-app", desktopMode);

const qc = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider defaultTheme="system" enableSystem enableColorScheme>
      <ReducedMotionProvider>
        <TooltipProvider delay={250}>
          <QueryClientProvider client={qc}>
            <BrowserRouter basename="/console">
              <App />
            </BrowserRouter>
          </QueryClientProvider>
        </TooltipProvider>
      </ReducedMotionProvider>
    </ThemeProvider>
  </React.StrictMode>
);
