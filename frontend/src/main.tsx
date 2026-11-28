import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { BootProvider } from "@/hooks/data";
import Dashboard from "@/pages/Dashboard";
import Live from "@/pages/Live";
import BrandKit from "@/pages/BrandKit";
import Projects from "@/pages/Projects";
import Queue from "@/pages/Queue";
import Usage from "@/pages/Usage";
import Director from "@/pages/Director";
import Editor from "@/pages/Editor";
import Project from "@/pages/Project";
import Settings from "@/pages/Settings";
import Social from "@/pages/Social";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BootProvider>
      <BrowserRouter basename="/studio">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/editor" element={<Editor />} />
          <Route path="/brand" element={<BrandKit />} />
          <Route path="/social" element={<Social />} />
          <Route path="/social/accounts" element={<Social />} />
          <Route path="/social/compose" element={<Social />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/usage" element={<Usage />} />
          <Route path="/live" element={<Live />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/p/:id" element={<Project />} />
          <Route path="/p/:id/clip/:cid" element={<Director />} />
          <Route path="*" element={<Dashboard />} />
        </Routes>
      </BrowserRouter>
    </BootProvider>
  </StrictMode>,
);
