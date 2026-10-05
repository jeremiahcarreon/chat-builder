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
import { DataSources } from "./pages/DataSources";
import { EmbedChat } from "./pages/EmbedChat";
import { Invite } from "./pages/Invite";
import { Login } from "./pages/Login";
import { MemberHome } from "./pages/MemberHome";
import { People } from "./pages/People";
import { Settings } from "./pages/Settings";
import { Usage } from "./pages/Usage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

function App() {
  return (
    <Routes>
      <Route path="/chat/:appId" element={<ChatPage />} />
      <Route path="/invite/:token" element={<Invite />} />
      <Route path="/embed/app/:appId" element={<EmbedChat />} />
      <Route path="*" element={<Dashboard />} />
    </Routes>
  );
}

function Dashboard() {
  const { data: user, isLoading } = useCurrentUser();
  if (isLoading) return null;
  if (!user) return <Login />;
  if (!user.is_admin) return <MemberHome />;

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/apps" element={<Apps />} />
        <Route path="/apps/:appId" element={<AppEditor />} />
        <Route path="/sources" element={<DataSources />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/people" element={<People />} />
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
