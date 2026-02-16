import type { ReactNode } from 'react';

interface CardProps {
  title: string;
  children: ReactNode;
  headerRight?: ReactNode;
}

export function Card({ title, children, headerRight }: CardProps) {
  return (
    <div className="border border-border">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-panel">
        <h3 className="text-muted font-semibold text-xs uppercase tracking-wider">{title}</h3>
        {headerRight}
      </div>
      <div className="p-3 bg-bg">
        {children}
      </div>
    </div>
  );
}
