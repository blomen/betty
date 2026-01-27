import { TerminalWindow } from '@/components/Terminal';
import { useBettingContext } from '@/hooks/useBettingContext';
import { useProfiles } from '@/hooks/useProfiles';

export default function App() {
  const { context, isLoading, refresh } = useBettingContext();
  const profilesState = useProfiles();

  return (
    <div className="h-screen w-screen overflow-hidden">
      <TerminalWindow
        context={context}
        onRefresh={refresh}
        isContextLoading={isLoading}
        profilesState={profilesState}
      />
    </div>
  );
}
