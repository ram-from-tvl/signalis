import { Routes, Route } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { DashboardPage } from "@/pages/DashboardPage"
import { LeadPipelinePage } from "@/pages/LeadPipelinePage"
import { LeadDetailPage } from "@/pages/LeadDetailPage"
import { DataSourcesPage } from "@/pages/DataSourcesPage"
import { SetupPage } from "@/pages/SetupPage"

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/leads" element={<LeadPipelinePage />} />
        <Route path="/leads/:leadId" element={<LeadDetailPage />} />
        <Route path="/data" element={<DataSourcesPage />} />
        <Route path="/setup" element={<SetupPage />} />
      </Route>
    </Routes>
  )
}
