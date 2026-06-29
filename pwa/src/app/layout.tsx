import type { Metadata, Viewport } from 'next';
import './globals.css';
import { ClientLayout } from './client-layout';

export const metadata: Metadata = {
  title: 'JARVIS',
  description: "Assistant personnel d'Elias",
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: 'JARVIS',
  },
  icons: {
    apple: '/icons/icon-192.png',
  },
};

export const viewport: Viewport = {
  themeColor: '#0a0a0f',
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: 'cover',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="fr">
      <head>
        <link rel="apple-touch-icon" href="/icons/icon-192.png" />
      </head>
      <body className="min-h-screen bg-[#0a0a0f] text-text-primary antialiased">
        <ClientLayout>{children}</ClientLayout>
      </body>
    </html>
  );
}
