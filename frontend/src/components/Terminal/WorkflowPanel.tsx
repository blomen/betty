import { useState, useEffect, useRef } from 'react';
import type {
  DropdownWorkflowState,
  DropdownOption,
  BankrollWorkflowState,
  BankrollOption,
} from '@/types';

interface WorkflowPanelProps {
  // Generic dropdown workflow (value, bets)
  dropdownWorkflow: DropdownWorkflowState;
  dropdownOptions: DropdownOption[];
  selectedIndex: number;
  onDropdownSelect: (option: DropdownOption) => void;
  onDropdownCancel: () => void;
  // Manual stake input
  manualStakeInput?: string;
  onManualStakeChange?: (value: string) => void;
  onManualStakeSubmit?: (value: string) => void;
  isManualStakeMode?: boolean;
  // Bankroll workflow
  bankrollWorkflow?: BankrollWorkflowState;
  bankrollOptions?: BankrollOption[];
  selectedBankrollIndex?: number;
  onBankrollSelect?: (option: BankrollOption) => void;
  onBankrollCancel?: () => void;
  // Bankroll amount input
  bankrollAmountInput?: string;
  onBankrollAmountChange?: (value: string) => void;
  onBankrollAmountSubmit?: () => void;
  isBankrollAmountMode?: boolean;
  // Bankroll reset confirmation
  bankrollConfirmInput?: string;
  onBankrollConfirmChange?: (value: string) => void;
  onBankrollConfirmSubmit?: () => void;
  isBankrollResetConfirmMode?: boolean;
}

export function WorkflowPanel({
  dropdownWorkflow,
  dropdownOptions,
  selectedIndex,
  onDropdownSelect,
  onDropdownCancel,
  manualStakeInput = '',
  onManualStakeChange,
  onManualStakeSubmit,
  isManualStakeMode = false,
  bankrollWorkflow,
  bankrollOptions = [],
  selectedBankrollIndex = 0,
  onBankrollSelect,
  onBankrollCancel,
  bankrollAmountInput = '',
  onBankrollAmountChange,
  onBankrollAmountSubmit,
  isBankrollAmountMode = false,
  bankrollConfirmInput = '',
  onBankrollConfirmChange,
  onBankrollConfirmSubmit,
  isBankrollResetConfirmMode = false,
}: WorkflowPanelProps) {
  const [isMinimized, setIsMinimized] = useState(false);
  const manualInputRef = useRef<HTMLInputElement>(null);
  const optionRefs = useRef<Map<number, HTMLButtonElement>>(new Map());

  // Check which workflow is active
  const isDropdownActive = dropdownWorkflow.type !== 'idle';
  const isBankrollActive = bankrollWorkflow?.step !== 'idle' && bankrollWorkflow?.step !== undefined;

  // Reset minimized state when workflow changes
  useEffect(() => {
    setIsMinimized(false);
  }, [dropdownWorkflow.type, bankrollWorkflow?.step]);

  // Focus input when entering manual mode (dropdown or bankroll)
  useEffect(() => {
    if ((isManualStakeMode || isBankrollAmountMode || isBankrollResetConfirmMode) && manualInputRef.current) {
      manualInputRef.current.focus();
    }
  }, [isManualStakeMode, isBankrollAmountMode, isBankrollResetConfirmMode]);

  // Auto-scroll to keep selected option visible
  const currentSelectedIndex = isDropdownActive
    ? selectedIndex
    : selectedBankrollIndex;

  useEffect(() => {
    const selectedButton = optionRefs.current.get(currentSelectedIndex);
    if (selectedButton) {
      selectedButton.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [currentSelectedIndex]);

  // Get workflow label
  const getWorkflowLabel = (): string => {
    if (isDropdownActive) {
      switch (dropdownWorkflow.type) {
        case 'value':
          return 'Value Bets';
        default:
          return 'Select';
      }
    }
    if (isBankrollActive) {
      return 'Bankroll';
    }
    return 'Select';
  };

  // Get step label
  const getStepLabel = (): string => {
    if (isDropdownActive) {
      switch (dropdownWorkflow.step) {
        case 'select-provider':
          return 'Select provider';
        case 'select-opportunity':
          return 'Select opp';
        case 'select-stake':
          return 'Select stake';
        case 'manual-stake':
          return 'Enter stake';
        case 'confirm':
          return 'Confirm';
        default:
          return '';
      }
    }
    if (isBankrollActive && bankrollWorkflow) {
      switch (bankrollWorkflow.step) {
        case 'select-action':
          return 'Select action';
        case 'select-provider':
          return 'Select provider';
        case 'enter-amount':
          return 'Enter amount';
        case 'select-setting':
          return 'Select setting';
        case 'select-value':
          return 'Select value';
        case 'confirm-reset':
          return 'Confirm reset';
        default:
          return '';
      }
    }
    return '';
  };

  // Handle cancel
  const handleCancel = () => {
    if (isDropdownActive) {
      onDropdownCancel();
    } else if (isBankrollActive && onBankrollCancel) {
      onBankrollCancel();
    }
  };

  // Render nothing if no workflow is active
  if (!isDropdownActive && !isBankrollActive) {
    return null;
  }

  // Get options to display
  const options = isDropdownActive
    ? dropdownOptions
    : bankrollOptions;

  return (
    <div className="border border-border rounded-lg bg-panel font-mono text-sm my-2">
      {/* Header - clickable to minimize */}
      <div
        className="flex items-center justify-between px-3 py-2 border-b border-border cursor-pointer hover:bg-panel2"
        onClick={() => setIsMinimized(!isMinimized)}
      >
        <div className="flex items-center gap-2">
          <span className="text-muted2 text-xs">
            {isMinimized ? '>' : 'v'}
          </span>
          <span className="text-accent text-xs font-medium">
            {getWorkflowLabel()}
          </span>
          <span className="text-muted2 text-xs">
            {getStepLabel()} ({options.length})
          </span>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            handleCancel();
          }}
          className="text-muted2 hover:text-text text-xs"
        >
          esc
        </button>
      </div>

      {/* Content - collapsible */}
      {!isMinimized && (
        <div className="p-2 max-h-64 overflow-y-auto">
          {/* Manual stake input mode */}
          {isManualStakeMode ? (
            <div className="flex items-center gap-2">
              <span className="text-muted">$</span>
              <input
                ref={manualInputRef}
                type="number"
                value={manualStakeInput}
                onChange={(e) => onManualStakeChange?.(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    onManualStakeSubmit?.(manualStakeInput);
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    handleCancel();
                  }
                }}
                placeholder="Enter stake amount..."
                className="flex-1 bg-bg border border-border rounded px-2 py-1.5 text-text placeholder-muted2 outline-none focus:border-accent"
              />
              <button
                onClick={() => onManualStakeSubmit?.(manualStakeInput)}
                className="px-3 py-1.5 rounded bg-accentBg text-accent hover:bg-accent/30 transition-colors"
              >
                OK
              </button>
              <button
                onClick={handleCancel}
                className="px-2 py-1.5 rounded text-muted hover:text-text hover:bg-panel2 transition-colors"
              >
                [cancel]
              </button>
            </div>
          ) : isBankrollAmountMode ? (
            /* Bankroll amount input mode */
            <div className="flex items-center gap-2">
              <span className="text-muted">$</span>
              <input
                ref={manualInputRef}
                type="number"
                value={bankrollAmountInput}
                onChange={(e) => onBankrollAmountChange?.(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    onBankrollAmountSubmit?.();
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    handleCancel();
                  }
                }}
                placeholder="Enter amount..."
                className="flex-1 bg-bg border border-border rounded px-2 py-1.5 text-text placeholder-muted2 outline-none focus:border-accent"
              />
              <button
                onClick={() => onBankrollAmountSubmit?.()}
                className="px-3 py-1.5 rounded bg-accentBg text-accent hover:bg-accent/30 transition-colors"
              >
                OK
              </button>
              <button
                onClick={handleCancel}
                className="px-2 py-1.5 rounded text-muted hover:text-text hover:bg-panel2 transition-colors"
              >
                [cancel]
              </button>
            </div>
          ) : isBankrollResetConfirmMode ? (
            /* Bankroll reset confirmation mode */
            <div className="flex items-center gap-2">
              <span className="text-muted">Type RESET:</span>
              <input
                ref={manualInputRef}
                type="text"
                value={bankrollConfirmInput}
                onChange={(e) => onBankrollConfirmChange?.(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    onBankrollConfirmSubmit?.();
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    handleCancel();
                  }
                }}
                placeholder="RESET"
                className="flex-1 bg-bg border border-border rounded px-2 py-1.5 text-text placeholder-muted2 outline-none focus:border-accent uppercase"
              />
              <button
                onClick={() => onBankrollConfirmSubmit?.()}
                className="px-3 py-1.5 rounded bg-error/20 text-error hover:bg-error/30 transition-colors"
              >
                RESET
              </button>
              <button
                onClick={handleCancel}
                className="px-2 py-1.5 rounded text-muted hover:text-text hover:bg-panel2 transition-colors"
              >
                [cancel]
              </button>
            </div>
          ) : (
            /* Options list */
            <div className="flex flex-col gap-1">
              {isDropdownActive
                ? dropdownOptions.map((opt, idx) => (
                    <button
                      key={`${opt.id}-${idx}`}
                      ref={(el) => { if (el) optionRefs.current.set(idx, el); }}
                      onClick={() => onDropdownSelect(opt)}
                      className={`text-left px-2 py-1.5 rounded flex items-center gap-2 transition-colors ${
                        idx === currentSelectedIndex
                          ? 'bg-accentBg border border-accentBorder text-accent'
                          : 'bg-transparent border border-transparent hover:bg-panel2 text-text'
                      }`}
                    >
                      <span className="font-medium truncate">{opt.label}</span>
                      {opt.sublabel && (
                        <span className="text-xs text-muted truncate">
                          {opt.sublabel}
                        </span>
                      )}
                    </button>
                  ))
                : bankrollOptions.map((opt, idx) => (
                    <button
                      key={`${opt.id}-${idx}`}
                      ref={(el) => { if (el) optionRefs.current.set(idx, el); }}
                      onClick={() => onBankrollSelect?.(opt)}
                      className={`text-left px-2 py-1.5 rounded flex items-center gap-2 transition-colors ${
                        idx === currentSelectedIndex
                          ? 'bg-accentBg border border-accentBorder text-accent'
                          : 'bg-transparent border border-transparent hover:bg-panel2 text-text'
                      }`}
                    >
                      <span className="font-medium truncate">{opt.label}</span>
                      {opt.sublabel && (
                        <span className="text-xs text-muted truncate">
                          {opt.sublabel}
                        </span>
                      )}
                    </button>
                  ))}
              {/* Cancel option */}
              <button
                onClick={handleCancel}
                className="text-left px-2 py-1.5 rounded flex items-center gap-2 transition-colors bg-transparent border border-transparent hover:bg-panel2 text-muted hover:text-text"
              >
                <span className="font-medium">[cancel]</span>
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
