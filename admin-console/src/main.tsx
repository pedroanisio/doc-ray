import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { ServicesProvider, createDefaultServices } from "./services";
import "./styles.css";

const services = createDefaultServices();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ServicesProvider services={services}>
      <App />
    </ServicesProvider>
  </StrictMode>,
);
