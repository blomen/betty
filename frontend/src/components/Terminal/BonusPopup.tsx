interface BonusPopupProps {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}

export function BonusPopup({ title, children, onClose }: BonusPopupProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-panel2 border border-border rounded-lg shadow-xl max-w-sm w-full mx-4"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-5 py-3 border-b border-border">
          <h3 className="text-sm font-semibold text-text">{title}</h3>
        </div>
        <div className="px-5 py-4">
          {children}
        </div>
      </div>
    </div>
  );
}
