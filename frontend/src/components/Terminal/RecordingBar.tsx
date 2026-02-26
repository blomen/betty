import { useRecorder } from '@/contexts/RecorderContext';

export function RecordingBar() {
  const { isRecording, recordingProvider, recordingWorkflow, actionCount, stopAutoRecord } = useRecorder();

  if (!isRecording) return null;

  return (
    <div className="flex items-center gap-3 px-3 py-1.5 bg-panel border-b border-border text-xs flex-shrink-0">
      <span className="flex items-center gap-1.5">
        <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
        <span className="text-red-400 font-medium">REC</span>
      </span>
      {recordingProvider && (
        <span className="font-medium" style={{ color: '#F97316' }}>{recordingProvider}</span>
      )}
      {recordingWorkflow && (
        <span className="text-muted">{recordingWorkflow.replace(/_/g, ' ')}</span>
      )}
      <span className="text-muted">&mdash; {actionCount} actions</span>
      <div className="ml-auto">
        <button
          onClick={stopAutoRecord}
          className="px-2.5 py-1 rounded text-[11px] font-medium bg-green-600 text-white hover:bg-green-500 transition-colors"
        >
          Done &#10003;
        </button>
      </div>
    </div>
  );
}
