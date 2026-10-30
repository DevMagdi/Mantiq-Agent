import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { type ReactNode } from "react";
import { Shell } from "./components/Shell";
import { useAuth } from "./lib/session";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import RequestsPage from "./pages/RequestsPage";
import RfqBoardPage from "./pages/RfqBoardPage";
import RfqDetailPage from "./pages/RfqDetailPage";
import ApprovalsPage from "./pages/ApprovalsPage";
import SuppliersPage from "./pages/SuppliersPage";
import PurchaseOrdersPage from "./pages/PurchaseOrdersPage";
import PurchaseOrderDetailPage from "./pages/PurchaseOrderDetailPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import ActivityPage from "./pages/ActivityPage";
import SettingsPage from "./pages/SettingsPage";
import PublicFormPage from "./pages/PublicFormPage";
import PublicPortalPage from "./pages/PublicPortalPage";
import NotFoundPage from "./pages/NotFoundPage";

function Protected({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface">
        <div className="flex items-center gap-3 text-sm text-ink-muted">
          <span className="size-4 animate-spin rounded-full border-2 border-line border-t-gold" />
          Loading ProcureAI…
        </div>
      </div>
    );
  }
  if (status === "anonymous") return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      {/* No-login supplier surfaces: token in the URL is the credential. */}
      <Route path="/form/:token" element={<PublicFormPage />} />
      <Route path="/portal/:token" element={<PublicPortalPage />} />
      <Route
        element={
          <Protected>
            <Shell />
          </Protected>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="/requests" element={<RequestsPage />} />
        <Route path="/rfqs" element={<RfqBoardPage />} />
        <Route path="/rfqs/:id" element={<RfqDetailPage />} />
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/purchase-orders" element={<PurchaseOrdersPage />} />
        <Route path="/purchase-orders/:id" element={<PurchaseOrderDetailPage />} />
        <Route path="/suppliers" element={<SuppliersPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/activity" element={<ActivityPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
