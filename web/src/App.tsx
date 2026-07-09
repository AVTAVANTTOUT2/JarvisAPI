import { Suspense, lazy, type ReactNode } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { BigBrotherLayout } from '@/app/components/layout/BigBrotherLayout';
import { ChatView } from '@/app/components/views/ChatView';

// Lazy-loading : chaque vue devient un chunk séparé — recharts et les vues
// lourdes ne sont téléchargés qu'à la première navigation. ChatView reste
// eager : c'est la route par défaut.
const Dashboard = lazy(() => import('@/app/components/views/Dashboard').then(m => ({ default: m.Dashboard })));
const ContactsView = lazy(() => import('@/app/components/views/ContactsView').then(m => ({ default: m.ContactsView })));
const CalendarView = lazy(() => import('@/app/components/views/CalendarView').then(m => ({ default: m.CalendarView })));
const MapView = lazy(() => import('@/app/components/views/MapView').then(m => ({ default: m.MapView })));
const DocumentsView = lazy(() => import('@/app/components/views/DocumentsView').then(m => ({ default: m.DocumentsView })));
const AnalyticsView = lazy(() => import('@/app/components/views/AnalyticsView').then(m => ({ default: m.AnalyticsView })));
const SearchView = lazy(() => import('@/app/components/views/SearchView').then(m => ({ default: m.SearchView })));
const DataView = lazy(() => import('@/app/components/views/DataView').then(m => ({ default: m.DataView })));
const LogsView = lazy(() => import('@/app/components/views/LogsView').then(m => ({ default: m.LogsView })));
const VoiceView = lazy(() => import('@/app/components/views/VoiceView').then(m => ({ default: m.VoiceView })));
const MonitoringView = lazy(() => import('@/app/components/views/MonitoringView').then(m => ({ default: m.MonitoringView })));
const ControlView = lazy(() => import('@/app/components/views/ControlView'));
const TasksView = lazy(() => import('@/app/components/views/TasksView'));
const VoiceDebugView = lazy(() => import('@/app/components/views/VoiceDebugView'));
const MissionControl = lazy(() => import('@/pages/MissionControl'));

function S({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-full min-h-[40vh] text-sm text-muted-foreground font-mono">
          Chargement…
        </div>
      }
    >
      {children}
    </Suspense>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<BigBrotherLayout />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatView />} />
          <Route path="dashboard" element={<S><Dashboard /></S>} />
          <Route path="contacts" element={<S><ContactsView /></S>} />
          <Route path="calendar" element={<S><CalendarView /></S>} />
          <Route path="map" element={<S><MapView /></S>} />
          <Route path="documents" element={<S><DocumentsView /></S>} />
          <Route path="analytics" element={<S><AnalyticsView /></S>} />
          <Route path="search" element={<S><SearchView /></S>} />
          <Route path="data" element={<S><DataView /></S>} />
          <Route path="logs" element={<S><LogsView /></S>} />
          <Route path="voice" element={<S><VoiceView /></S>} />
          <Route path="monitoring" element={<S><MonitoringView /></S>} />
          <Route path="control" element={<S><ControlView /></S>} />
          <Route path="tasks" element={<S><TasksView /></S>} />
          <Route path="voice-debug" element={<S><VoiceDebugView /></S>} />
          <Route path="mission" element={<S><MissionControl /></S>} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
