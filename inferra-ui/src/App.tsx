import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { TopNav } from "./components/layout/TopNav";
import { ChatPage } from "./pages/chat/ChatPage";
import { KeysPage } from "./pages/keys/KeysPage";
import { AdaptersPage } from "./pages/adapters/AdaptersPage";
import { UsagePage } from "./pages/usage/UsagePage";
import { WorkersPage } from "./pages/workers/WorkersPage";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <div className="min-h-screen flex flex-col">
          <TopNav />
          <main className="flex-1">
            <Routes>
              <Route path="/" element={<Navigate to="/chat" replace />} />
              <Route path="/chat" element={<ChatPage />} />
              <Route path="/keys" element={<KeysPage />} />
              <Route path="/adapters" element={<AdaptersPage />} />
              <Route path="/usage" element={<UsagePage />} />
              <Route path="/workers" element={<WorkersPage />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </AuthProvider>
  );
}
