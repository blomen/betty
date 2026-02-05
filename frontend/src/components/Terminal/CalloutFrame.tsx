interface CalloutFrameProps {
  title?: string;
  children: React.ReactNode;
}

export function CalloutFrame({ title, children }: CalloutFrameProps) {
  return (
    <div className="border border-calloutBorder rounded-lg p-3 bg-bg">
      {title && (
        <div className="font-semibold text-text mb-2">{title}</div>
      )}
      <div className="font-mono text-sm text-text whitespace-pre-wrap">
        {children}
      </div>
    </div>
  );
}
