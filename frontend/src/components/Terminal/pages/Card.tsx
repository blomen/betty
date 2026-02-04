import type { ReactNode } from 'react';

interface CardProps {
  title: string;
  children: ReactNode;
  headerRight?: ReactNode;
}

export function Card({ title, children, headerRight }: CardProps) {
  return (
    <div className="bg-panel border border-border rounded-lg">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h3 className="text-text font-semibold text-sm">{title}</h3>
        {headerRight}
      </div>
      <div className="p-4">
        {children}
      </div>
    </div>
  );
}
