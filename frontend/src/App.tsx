import { TerminalWindow } from '@/components/Terminal';
import { useBettingContext } from '@/hooks/useBettingContext';

export default function App() {
  const { context, refresh } = useBettingContext();

  return (
    <div className="h-screen w-screen overflow-hidden">
      <TerminalWindow context={context} onRefresh={refresh} />
    </div>
  );
}
