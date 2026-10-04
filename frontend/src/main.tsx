import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { useCurrentUser } from "./auth";
import { Layout } from "./components/Layout";
import "./index.css";
import { AppEditor } from "./pages/AppEditor";
import { Apps } from "./pages/Apps";
import { ChatPage } from "./pages/ChatPage";
import { Login } from "./pages/Login";
import { Placeholder } from "./pages/Placeholder";
import { Settings } from "./pages/Settings";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

function App() {
  return (
    <Routes>
      <Route path="/chat/:appId" element={<ChatPage />} />
      <Route path="*" element={<Dashboard />} />
    </Routes>
  );
}

function Dashboard() {
  const { data: user, isLoading } = useCurrentUser();
  if (isLoading) return null;
  if (!user) return <Login />;

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/apps" element={<Apps />} />
        <Route path="/apps/:appId" element={<AppEditor />} />
        <Route path="/sources" element={<Placeholder title="Data Sources" step={3} />} />
        <Route path="/usage" element={<Placeholder title="Usage" step={6} />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/apps" replace />} />
      </Route>
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
