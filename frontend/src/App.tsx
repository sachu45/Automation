import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthGate } from "@/app/auth";
import { Layout } from "@/app/Layout";
import { AccountsPage } from "@/pages/Accounts";
import { AnalyticsPage } from "@/pages/Analytics";
import { CampaignDetailPage } from "@/pages/CampaignDetail";
import { CampaignEditorPage } from "@/pages/CampaignEditor";
import { CampaignsPage } from "@/pages/Campaigns";
import { DashboardPage } from "@/pages/Dashboard";
import { DiscoveryPage } from "@/pages/Discovery";
import { InboxPage } from "@/pages/Inbox";
import { LeadDetailPage } from "@/pages/LeadDetail";
import { LeadImportPage } from "@/pages/LeadImport";
import { LeadsPage } from "@/pages/Leads";
import { ListsPage } from "@/pages/Lists";
import { NotFoundPage } from "@/pages/NotFound";
import { SequencesPage } from "@/pages/Sequences";
import { SettingsPage } from "@/pages/Settings";
import { SuppressionsPage } from "@/pages/Suppressions";
import { TemplatesPage } from "@/pages/Templates";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: (count, err) => count < 1 && !(err && "status" in err && (err as { status: number }).status < 500), refetchOnWindowFocus: false },
    },
  });
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="leads" element={<LeadsPage />} />
        <Route path="leads/import" element={<LeadImportPage />} />
        <Route path="leads/:id" element={<LeadDetailPage />} />
        <Route path="lists" element={<ListsPage />} />
        <Route path="discovery" element={<DiscoveryPage />} />
        <Route path="campaigns" element={<CampaignsPage />} />
        <Route path="campaigns/new" element={<CampaignEditorPage />} />
        <Route path="campaigns/:id" element={<CampaignDetailPage />} />
        <Route path="campaigns/:id/edit" element={<CampaignEditorPage />} />
        <Route path="sequences" element={<SequencesPage />} />
        <Route path="templates" element={<TemplatesPage />} />
        <Route path="inbox" element={<InboxPage />} />
        <Route path="accounts" element={<AccountsPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="suppressions" element={<SuppressionsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export default function App({ client = makeQueryClient() }: { client?: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <AuthGate>
          <AppRoutes />
        </AuthGate>
      </BrowserRouter>
      <Toaster position="bottom-right" richColors closeButton />
    </QueryClientProvider>
  );
}
