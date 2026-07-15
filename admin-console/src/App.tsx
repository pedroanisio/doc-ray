import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/LoginPage";
import { DashboardPage } from "./pages/DashboardPage";
import { JobsPage } from "./pages/JobsPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { AuditPage } from "./pages/AuditPage";
import { BackupsPage } from "./pages/BackupsPage";
import { IdentityPage } from "./pages/IdentityPage";
import { BillingPage } from "./pages/BillingPage";

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            element={
              <ProtectedRoute>
                <Layout />
              </ProtectedRoute>
            }
          >
            <Route index element={<DashboardPage />} />
            <Route path="jobs" element={<JobsPage />} />
            <Route path="documents" element={<DocumentsPage />} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="backups" element={<BackupsPage />} />
            <Route path="identity" element={<IdentityPage />} />
            <Route path="billing" element={<BillingPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
