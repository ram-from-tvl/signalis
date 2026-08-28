import { Routes, Route } from "react-router-dom"
import { MotionConfig } from "motion/react"
import { AppShell } from "@/components/layout/AppShell"
import { DashboardPage } from "@/pages/DashboardPage"
import { LeadPipelinePage } from "@/pages/LeadPipelinePage"
import { LeadDetailPage } from "@/pages/LeadDetailPage"
import { DataSourcesPage } from "@/pages/DataSourcesPage"
import { SetupPage } from "@/pages/SetupPage"

export default function App() {
  return (
    // reducedMotion="user" makes every Motion transform/layout animation in
    // the app (the sliding nav/tab indicators, list entrances, etc.) honor
    // prefers-reduced-motion automatically, the same way index.css's ambient
    // background and the manual checks in click-spark/useCountUp already do.
    <MotionConfig reducedMotion="user">
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/leads" element={<LeadPipelinePage />} />
          <Route path="/leads/:leadId" element={<LeadDetailPage />} />
          <Route path="/data" element={<DataSourcesPage />} />
          <Route path="/setup" element={<SetupPage />} />
        </Route>
      </Routes>
    </MotionConfig>
  )
}
