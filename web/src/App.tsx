import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { BigBrotherLayout } from '@/app/components/layout/BigBrotherLayout';
import { Dashboard } from '@/app/components/views/Dashboard';
import { ContactsView } from '@/app/components/views/ContactsView';
import { ChatView } from '@/app/components/views/ChatView';
import { CalendarView } from '@/app/components/views/CalendarView';
import { MapView } from '@/app/components/views/MapView';
import { DocumentsView } from '@/app/components/views/DocumentsView';
import { AnalyticsView } from '@/app/components/views/AnalyticsView';
import { SearchView } from '@/app/components/views/SearchView';
import { DataView } from '@/app/components/views/DataView';
import { LogsView } from '@/app/components/views/LogsView';
import { VoiceView } from '@/app/components/views/VoiceView';
import { MonitoringView } from '@/app/components/views/MonitoringView';
import ControlView from '@/app/components/views/ControlView';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<BigBrotherLayout />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatView />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="contacts" element={<ContactsView />} />
          <Route path="calendar" element={<CalendarView />} />
          <Route path="map" element={<MapView />} />
          <Route path="documents" element={<DocumentsView />} />
          <Route path="analytics" element={<AnalyticsView />} />
          <Route path="search" element={<SearchView />} />
          <Route path="data" element={<DataView />} />
          <Route path="logs" element={<LogsView />} />
          <Route path="voice" element={<VoiceView />} />
          <Route path="monitoring" element={<MonitoringView />} />
          <Route path="control" element={<ControlView />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
