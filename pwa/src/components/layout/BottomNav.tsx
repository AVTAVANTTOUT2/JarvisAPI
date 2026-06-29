'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { CheckSquare, LayoutDashboard, Mail, Map, Settings } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

interface Tab {
  href: string;
  icon: LucideIcon;
  label: string;
}

const TABS: Tab[] = [
  { href: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { href: '/map', icon: Map, label: 'Carte' },
  { href: '/mails', icon: Mail, label: 'Mails' },
  { href: '/tasks', icon: CheckSquare, label: 'Taches' },
  { href: '/config', icon: Settings, label: 'Config' },
];

export function BottomNav() {
  const pathname = usePathname() || '';

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 bg-[rgba(10,10,15,0.92)] backdrop-blur-[30px] backdrop-saturate-[180%] border-t border-[rgba(255,255,255,0.06)]"
      style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
    >
      <div className="flex items-center justify-around h-[56px]">
        {TABS.map(({ href, icon: Icon, label }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className="flex flex-col items-center gap-1 flex-1 pt-1.5 active:opacity-60 transition-opacity"
            >
              <Icon
                size={22}
                className={active ? 'text-[#4A9EFF]' : 'text-[#444]'}
                strokeWidth={active ? 2.2 : 1.8}
              />
              <span
                className={`text-[10px] font-medium tracking-wide ${
                  active ? 'text-[#4A9EFF]' : 'text-[#444]'
                }`}
              >
                {label}
              </span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
