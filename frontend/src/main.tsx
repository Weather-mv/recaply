import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { BootProvider } from "@/hooks/data";
import Dashboard from "@/pages/Dashboard";
import Director from "@/pages/Director";
import Project from "@/pages/Project";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BootProvider>
      <BrowserRouter basename="/studio">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/p/:id" element={<Project />} />
          <Route path="/p/:id/clip/:cid" element={<Director />} />
          <Route path="*" element={<Dashboard />} />
        </Routes>
      </BrowserRouter>
    </BootProvider>
  </StrictMode>,
);
