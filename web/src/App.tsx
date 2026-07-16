import { Suspense, lazy, type ReactNode } from 'react';
import { LockGate } from '@jarvis/auth';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { BigBrotherLayout } from '@desktop/app/components/layout/BigBrotherLayout';
import { InstallPrompt } from '@desktop/app/components/pwa/InstallPrompt';
import { NotificationsPrompt } from '@desktop/app/components/pwa/NotificationsPrompt';
import { ChatView } from '@desktop/app/components/views/ChatView';
import { clearOfflineDB } from '@desktop/lib/offline/db';
import { initOfflineSync } from '@desktop/lib/offline/queue';

// Lazy-loading : chaque vue devient un chunk séparé — recharts et les vues
// lourdes ne sont téléchargés qu'à la première navigation. ChatView reste
// eager : c'est la route par défaut.
const Dashboard = lazy(() => import('@desktop/app/components/views/Dashboard').then(m => ({ default: m.Dashboard })));
const ContactsView = lazy(() => import('@desktop/app/components/views/ContactsView').then(m => ({ default: m.ContactsView })));
const CalendarView = lazy(() => import('@desktop/app/components/views/CalendarView').then(m => ({ default: m.CalendarView })));
const MapView = lazy(() => import('@desktop/app/components/views/MapView').then(m => ({ default: m.MapView })));
const DocumentsView = lazy(() => import('@desktop/app/components/views/DocumentsView').then(m => ({ default: m.DocumentsView })));
const AnalyticsView = lazy(() => import('@desktop/app/components/views/AnalyticsView').then(m => ({ default: m.AnalyticsView })));
const SearchView = lazy(() => import('@desktop/app/components/views/SearchView').then(m => ({ default: m.SearchView })));
const DataView = lazy(() => import('@desktop/app/components/views/DataView').then(m => ({ default: m.DataView })));
const LogsView = lazy(() => import('@desktop/app/components/views/LogsView').then(m => ({ default: m.LogsView })));
const VoiceView = lazy(() => import('@desktop/app/components/views/VoiceView').then(m => ({ default: m.VoiceView })));
const MonitoringView = lazy(() => import('@desktop/app/components/views/MonitoringView').then(m => ({ default: m.MonitoringView })));
const CognitiveView = lazy(() => import('@desktop/app/components/views/CognitiveView'));
const ControlView = lazy(() => import('@desktop/app/components/views/ControlView'));
const TasksView = lazy(() => import('@desktop/app/components/views/TasksView'));
const VoiceDebugView = lazy(() => import('@desktop/app/components/views/VoiceDebugView'));
const MissionControl = lazy(() => import('@desktop/pages/MissionControl'));
const MobileDevicesView = lazy(() => import('@desktop/app/components/views/MobileDevicesView'));

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
    <LockGate
      onAuthenticated={() => initOfflineSync(() => {
        window.dispatchEvent(new CustomEvent('jarvis:offline-sync-done'));
      })}
      onUnauthenticated={() => { void clearOfflineDB(); }}
    >
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
            <Route path="cognitive" element={<S><CognitiveView /></S>} />
            <Route path="control" element={<S><ControlView /></S>} />
            <Route path="tasks" element={<S><TasksView /></S>} />
            <Route path="voice-debug" element={<S><VoiceDebugView /></S>} />
            <Route path="mission" element={<S><MissionControl /></S>} />
            <Route path="mobile" element={<S><MobileDevicesView /></S>} />
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <InstallPrompt />
      <NotificationsPrompt />
    </LockGate>
  );
}
