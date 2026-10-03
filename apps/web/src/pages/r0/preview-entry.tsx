import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import WorkflowR0PreviewPage from "../WorkflowR0PreviewPage";
import { applyAppThemeCssVariables } from "@/theme/appTheme";
import "@/styles/global.css";

// Standalone development preview: deliberately does not mount auth, project queries or Agent.
applyAppThemeCssVariables(document.documentElement);
const root = document.getElementById("root");
if (root) createRoot(root).render(<BrowserRouter><WorkflowR0PreviewPage/></BrowserRouter>);
