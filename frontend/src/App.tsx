import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { type ReactNode } from "react";
import { Shell } from "./components/Shell";
import { RequirePermission } from "./components/Guard";
import { PERM } from "./lib/permissions";
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
import JournalPage from "./pages/JournalPage";
import AccountingReportsPage from "./pages/AccountingReportsPage";
import AccountingSetupPage from "./pages/AccountingSetupPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import ActivityPage from "./pages/ActivityPage";
import SettingsPage from "./pages/SettingsPage";
import InboxPage from "./pages/InboxPage";
import TemplatesPage from "./pages/TemplatesPage";
import VoiceCallsPage from "./pages/VoiceCallsPage";
import AssistantPage from "./pages/AssistantPage";
import PublicFormPage from "./pages/PublicFormPage";
import PublicPortalPage from "./pages/PublicPortalPage";
import NotFoundPage from "./pages/NotFoundPage";

/**
 * A screen that only exists when the deployment has the capability behind it.
 *
 * Accounting is a deployment-level switch, not a tenant permission: when it is off the API has no
 * accounting route at all, so the three screens must behave exactly like an unknown URL — the normal
 * 404 — instead of a permission panel or a "coming soon" placeholder. The flag arrives through the
 * existing capability mechanism (`/auth/me` → permissions), so one build serves both modes.
 */
function CapabilityRoute({ permission, children }: { permission: string; children: ReactNode }) {
  const { can } = useAuth();
  if (!can(permission)) return <NotFoundPage />;
  return <>{children}</>;
}

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
        <Route index element={
            <RequirePermission permission={PERM.reportRead}>
              <DashboardPage />
            </RequirePermission>
          } />
        <Route path="/requests" element={
            <RequirePermission permission={PERM.prRead}>
              <RequestsPage />
            </RequirePermission>
          } />
        <Route path="/rfqs" element={
            <RequirePermission permission={PERM.rfqRead}>
              <RfqBoardPage />
            </RequirePermission>
          } />
        <Route path="/rfqs/:id" element={
            <RequirePermission permission={PERM.rfqRead}>
              <RfqDetailPage />
            </RequirePermission>
          } />
        <Route path="/approvals" element={
            <RequirePermission permission={PERM.approvalRead}>
              <ApprovalsPage />
            </RequirePermission>
          } />
        <Route path="/purchase-orders" element={
            <RequirePermission permission={PERM.poRead}>
              <PurchaseOrdersPage />
            </RequirePermission>
          } />
        <Route path="/purchase-orders/:id" element={
            <RequirePermission permission={PERM.poRead}>
              <PurchaseOrderDetailPage />
            </RequirePermission>
          } />
        <Route path="/suppliers" element={
            <RequirePermission permission={PERM.supplierRead}>
              <SuppliersPage />
            </RequirePermission>
          } />
        {/* Hidden with ACCOUNTING_ENABLED=false: a 404, exactly like an unknown URL. */}
        <Route path="/accounting" element={
            <CapabilityRoute permission={PERM.accountingRead}>
              <JournalPage />
            </CapabilityRoute>
          } />
        <Route path="/accounting/reports" element={
            <CapabilityRoute permission={PERM.costRead}>
              <AccountingReportsPage />
            </CapabilityRoute>
          } />
        <Route path="/accounting/setup" element={
            <CapabilityRoute permission={PERM.accountingRead}>
              <AccountingSetupPage />
            </CapabilityRoute>
          } />
        <Route path="/analytics" element={
            <RequirePermission permission={PERM.reportRead}>
              <AnalyticsPage />
            </RequirePermission>
          } />
        <Route path="/activity" element={
            <RequirePermission permission={PERM.auditRead}>
              <ActivityPage />
            </RequirePermission>
          } />
        <Route path="/inbox" element={
            <RequirePermission permission={PERM.inboxRead}>
              <InboxPage />
            </RequirePermission>
          } />
        <Route path="/templates" element={
            <RequirePermission permission={PERM.rfqRead}>
              <TemplatesPage />
            </RequirePermission>
          } />
        <Route path="/voice" element={
            <RequirePermission permission={PERM.voiceCall}>
              <VoiceCallsPage />
            </RequirePermission>
          } />
        <Route path="/assistant" element={
            <RequirePermission permission={PERM.assistantUse}>
              <AssistantPage />
            </RequirePermission>
          } />
        <Route path="/settings" element={
            <RequirePermission permission={PERM.settingsRead}>
              <SettingsPage />
            </RequirePermission>
          } />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
