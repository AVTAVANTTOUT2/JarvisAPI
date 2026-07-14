'use client'

import dynamic from 'next/dynamic'
import type { ComponentType } from 'react'

import { ClientLayout } from '@mobile/app/client-layout'

const DashboardPage = dynamic(() => import('@mobile/app/dashboard/page'))
const MapPage = dynamic(() => import('@mobile/app/map/page'), { ssr: false })
const MailsPage = dynamic(() => import('@mobile/app/mails/page'))
const TasksPage = dynamic(() => import('@mobile/app/tasks/page'))
const ConfigPage = dynamic(() => import('@mobile/app/config/page'))

const MOBILE_PAGES: Record<string, ComponentType> = {
  dashboard: DashboardPage,
  map: MapPage,
  mails: MailsPage,
  tasks: TasksPage,
  config: ConfigPage,
}

export function MobileApp({ segment }: { segment: string }) {
  const Page = MOBILE_PAGES[segment] ?? DashboardPage
  return (
    <ClientLayout>
      <Page />
    </ClientLayout>
  )
}
