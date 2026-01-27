import { TerminalWindow } from '@/components/Terminal';
import { useBettingContext } from '@/hooks/useBettingContext';

export default function App() {
  const { context, isLoading, refresh } = useBettingContext();

  return (
    <div className="h-screen w-screen overflow-hidden">
      <TerminalWindow
        context={context}
        onRefresh={refresh}
        isContextLoading={isLoading}
      />
    </div>
  );
}
