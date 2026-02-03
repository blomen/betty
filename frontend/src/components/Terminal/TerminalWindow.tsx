import { useEffect, useRef, useMemo, useCallback, useState } from 'react';
import type { BettingContext, Message } from '@/types';
import { useChat } from '@/hooks/useChat';
import { useBankroll } from '@/hooks/useBankroll';
import { useExtraction } from '@/hooks/useExtraction';
import { useBonusWorkflow } from '@/hooks/useBonusWorkflow';
import { useDropdownWorkflow } from '@/hooks/useDropdownWorkflow';
import { useBankrollWorkflow } from '@/hooks/useBankrollWorkflow';
import { useProfiles } from '@/hooks/useProfiles';
import { TerminalInput } from './TerminalInput';
import { ChatMessage } from './ChatMessage';
import { WelcomeMessage } from './WelcomeMessage';
import { ExtractionProgressMessage } from './ExtractionProgressMessage';
import { CommandPanel } from './CommandPanel';
import { WorkflowPanel } from './WorkflowPanel';
import { createCommandRegistry } from '@/utils/commands';
import { formatStatsReport } from '@/utils/formatters';
import { api } from '@/services/api';

interface TerminalWindowProps {
  context: BettingContext;
  onRefresh: () => void;
}

export function TerminalWindow({ context, onRefresh }: TerminalWindowProps) {
  const { messages, setMessages, isLoading, sendMessage, stopGeneration, clearMessages } = useChat();
  const { exposure } = useBankroll(30000);
  const { activeProfile } = useProfiles();
  const { runExtraction } = useExtraction();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Command panel state
  const [showCommandPanel, setShowCommandPanel] = useState(false);
  const [inputFilter, setInputFilter] = useState('');
  const [autofillValue, setAutofillValue] = useState<string | undefined>(undefined);
  const autofillCounter = useRef(0);

  // Extraction handler
  const handleRunExtraction = useCallback(async (providers?: string) => {
    const providerList = providers || context.providers.map(p => p.id).join(',');

    try {
      const generateId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
      const progressMessage: Message = {
        id: generateId(),
        role: 'assistant',
        content: '',
        timestamp: new Date(),
        isStreaming: true,
        isExtraction: true,
      };

      setMessages((prev: Message[]) => [...prev, progressMessage]);
      await runExtraction(providerList);
    } catch (err) {
      sendMessage(`[!] Extraction failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  }, [runExtraction, sendMessage, context.providers, setMessages]);

  // Workflow hooks
  const bonusWorkflow = useBonusWorkflow({
    providers: context.providers,
    sendMessage,
    onRefresh,
  });

  const dropdownWorkflow = useDropdownWorkflow({
    providers: context.providers,
    exposure,
    sendMessage,
    onRefresh,
    onRunExtraction: handleRunExtraction,
  });

  const bankrollWorkflow = useBankrollWorkflow({
    exposure,
    profile: activeProfile,
    sendMessage,
    onRefresh,
  });

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Extraction complete handler
  const handleExtractionComplete = useCallback(async () => {
    try {
      const status = await api.getExtractionProgress();

      if (status.total_events === 0 && status.total_odds === 0) {
        sendMessage(
          `[!] Extraction complete - no new data. Try again later.`
        );
      } else {
        sendMessage(
          `[+] Done: ${status.total_events} events, ${status.total_odds} odds. Try /arb or /value.`
        );
      }
      onRefresh();
    } catch (err) {
      sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown'}`);
    }
  }, [sendMessage, onRefresh]);

  // Simple command handlers
  const handleShowStats = useCallback(async () => {
    try {
      const stats = await api.getBankrollStats();
      sendMessage(formatStatsReport(stats));
    } catch (err) {
      sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown'}`);
    }
  }, [sendMessage]);

  // Command registry
  const commandRegistry = useMemo(
    () =>
      createCommandRegistry({
        onShowStats: handleShowStats,
        onClear: clearMessages,
        onBonusCommand: bonusWorkflow.start,
        onExtractWorkflow: dropdownWorkflow.startExtract,
        onArbWorkflow: dropdownWorkflow.startArb,
        onValueWorkflow: dropdownWorkflow.startValue,
        onBetsWorkflow: dropdownWorkflow.startBets,
        onBankrollWorkflow: bankrollWorkflow.start,
      }),
    [handleShowStats, clearMessages, bonusWorkflow.start,
     dropdownWorkflow.startExtract, dropdownWorkflow.startArb, dropdownWorkflow.startValue, dropdownWorkflow.startBets,
     bankrollWorkflow.start]
  );

  const commands = useMemo(() => Object.values(commandRegistry), [commandRegistry]);

  // Command execution
  const handleCommand = useCallback((command: string, args: string) => {
    const cmd = commandRegistry[command];

    if (!cmd) {
      sendMessage(`Unknown: /${command}. Type / to see commands.`);
      return;
    }

    // Commands with args support
    if (command === 'extract' && args) {
      handleRunExtraction(args);
      return;
    }

    try {
      cmd.execute();
    } catch (err) {
      sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown'}`);
    }
  }, [commandRegistry, sendMessage, handleRunExtraction]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === 'l' && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        clearMessages();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [clearMessages]);

  // UI handlers
  const handleCommandSelect = useCallback((commandName: string) => {
    setShowCommandPanel(false);
    setInputFilter('');
    handleCommand(commandName, '');
  }, [handleCommand]);

  const handleInputChange = useCallback((value: string) => {
    setInputFilter(value);
    setShowCommandPanel(value.startsWith('/'));
  }, []);

  const handleAutofill = useCallback((commandName: string) => {
    const value = `/${commandName}`;
    autofillCounter.current += 1;
    setAutofillValue(`${value}#${autofillCounter.current}`);
    setInputFilter(value);
  }, []);

  return (
    <div className="flex flex-col h-full bg-terminal-bg">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto">
        <div className="w-full">
          {messages.length === 0 && !showCommandPanel ? (
            <WelcomeMessage />
          ) : (
            <div className="pb-4">
              {messages.map((message) =>
                message.isExtraction ? (
                  <div key={message.id} className="py-4 px-4">
                    <div className="flex gap-3">
                      <span className="text-terminal-accent flex-shrink-0">*</span>
                      <div className="flex-1 min-w-0">
                        <ExtractionProgressMessage onComplete={handleExtractionComplete} />
                      </div>
                    </div>
                  </div>
                ) : (
                  <ChatMessage key={message.id} message={message} />
                )
              )}

              {showCommandPanel && (
                <div className="px-4">
                  <CommandPanel
                    commands={commands}
                    isOpen={showCommandPanel}
                    onClose={() => { setShowCommandPanel(false); setInputFilter(''); }}
                    onSelect={handleCommandSelect}
                    onAutofill={handleAutofill}
                    filter={inputFilter}
                  />
                </div>
              )}

              {(dropdownWorkflow.isActive || bonusWorkflow.isActive || bankrollWorkflow.isActive) && (
                <div className="px-4">
                  <WorkflowPanel
                    dropdownWorkflow={dropdownWorkflow.workflow}
                    dropdownOptions={dropdownWorkflow.options}
                    selectedIndex={dropdownWorkflow.selectedIndex}
                    onDropdownSelect={dropdownWorkflow.select}
                    onDropdownCancel={dropdownWorkflow.cancel}
                    manualStakeInput={dropdownWorkflow.manualStakeInput}
                    onManualStakeChange={dropdownWorkflow.setManualStakeInput}
                    onManualStakeSubmit={dropdownWorkflow.submitManualStake}
                    isManualStakeMode={dropdownWorkflow.isManualStakeMode}
                    bonusWorkflow={bonusWorkflow.workflow}
                    bonusOptions={bonusWorkflow.options}
                    selectedBonusIndex={bonusWorkflow.selectedIndex}
                    onBonusSelect={bonusWorkflow.select}
                    onBonusCancel={bonusWorkflow.cancel}
                    bonusManualStakeInput={bonusWorkflow.manualStakeInput}
                    onBonusManualStakeChange={bonusWorkflow.setManualStakeInput}
                    onBonusManualStakeSubmit={bonusWorkflow.submitManualStake}
                    isBonusManualStakeMode={bonusWorkflow.isManualStakeMode}
                    bankrollWorkflow={bankrollWorkflow.workflow}
                    bankrollOptions={bankrollWorkflow.options}
                    selectedBankrollIndex={bankrollWorkflow.selectedIndex}
                    onBankrollSelect={bankrollWorkflow.select}
                    onBankrollCancel={bankrollWorkflow.cancel}
                    bankrollAmountInput={bankrollWorkflow.amountInput}
                    onBankrollAmountChange={bankrollWorkflow.setAmountInput}
                    onBankrollAmountSubmit={bankrollWorkflow.submitAmount}
                    isBankrollAmountMode={bankrollWorkflow.isAmountMode}
                    bankrollConfirmInput={bankrollWorkflow.confirmInput}
                    onBankrollConfirmChange={bankrollWorkflow.setConfirmInput}
                    onBankrollConfirmSubmit={bankrollWorkflow.submitResetConfirmation}
                    isBankrollResetConfirmMode={bankrollWorkflow.isResetConfirmMode}
                  />
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>
          )}
        </div>
      </div>

      {/* Input */}
      <div className="w-full">
        <TerminalInput
          onSend={sendMessage}
          onCommand={(cmd, args) => { setShowCommandPanel(false); setInputFilter(''); handleCommand(cmd, args); }}
          commands={commands}
          onStop={stopGeneration}
          isLoading={isLoading}
          onSlashTyped={() => setShowCommandPanel(true)}
          onInputChange={handleInputChange}
          autofillValue={autofillValue}
          bonusWorkflow={bonusWorkflow.workflow}
          bonusOptions={bonusWorkflow.options}
          onBonusSelect={bonusWorkflow.select}
          onBonusCancel={bonusWorkflow.cancel}
          dropdownWorkflow={dropdownWorkflow.workflow}
          dropdownOptions={dropdownWorkflow.options}
          onDropdownSelect={dropdownWorkflow.select}
          onDropdownCancel={dropdownWorkflow.cancel}
          selectedDropdownIndex={dropdownWorkflow.selectedIndex}
          selectedBonusIndex={bonusWorkflow.selectedIndex}
          onSelectedDropdownIndexChange={dropdownWorkflow.setSelectedIndex}
          onSelectedBonusIndexChange={bonusWorkflow.setSelectedIndex}
          bankrollWorkflow={bankrollWorkflow.workflow}
          bankrollOptions={bankrollWorkflow.options}
          onBankrollSelect={bankrollWorkflow.select}
          onBankrollCancel={bankrollWorkflow.cancel}
          selectedBankrollIndex={bankrollWorkflow.selectedIndex}
          onSelectedBankrollIndexChange={bankrollWorkflow.setSelectedIndex}
        />
      </div>
    </div>
  );
}
