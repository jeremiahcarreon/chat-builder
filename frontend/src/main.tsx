import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { useCurrentUser } from "./auth";
import { Layout } from "./components/Layout";
import "./index.css";
import { Login } from "./pages/Login";
import { Placeholder } from "./pages/Placeholder";
import { Settings } from "./pages/Settings";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

function App() {
  const { data: user, isLoading } = useCurrentUser();
  if (isLoading) return null;
  if (!user) return <Login />;

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/apps" element={<Placeholder title="Chat Apps" step={2} />} />
        <Route path="/sources" element={<Placeholder title="Data Sources" step={3} />} />
        <Route path="/usage" element={<Placeholder title="Usage" step={6} />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/settings" replace />} />
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
