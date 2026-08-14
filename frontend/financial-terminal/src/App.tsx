import { Routes, Route, Navigate } from 'react-router-dom'
import TerminalDashboard from './pages/TerminalDashboard'
import NumericalAnalysisAlert from './pages/NumericalAnalysisAlert'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<TerminalDashboard />} />
      <Route path="/numerical-alert" element={<NumericalAnalysisAlert />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
