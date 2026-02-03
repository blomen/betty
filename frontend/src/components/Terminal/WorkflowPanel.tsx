import { useState, useEffect, useRef } from 'react';
import type {
  DropdownWorkflowState,
  DropdownOption,
  BonusWorkflowState,
  BonusDropdownOption,
  BankrollWorkflowState,
  BankrollOption,
} from '@/types';

interface WorkflowPanelProps {
  // Generic dropdown workflow (extract, arb, value)
  dropdownWorkflow: DropdownWorkflowState;
  dropdownOptions: DropdownOption[];
  selectedIndex: number;
  onDropdownSelect: (option: DropdownOption) => void;
  onDropdownCancel: () => void;
  // Manual stake input (shared between dropdown and bonus workflows)
  manualStakeInput?: string;
  onManualStakeChange?: (value: string) => void;
  onManualStakeSubmit?: (value: string) => void;
  isManualStakeMode?: boolean;
  // Bonus workflow
  bonusWorkflow?: BonusWorkflowState;
  bonusOptions?: BonusDropdownOption[];
  selectedBonusIndex?: number;
  onBonusSelect?: (option: BonusDropdownOption) => void;
  onBonusCancel?: () => void;
  // Bonus manual stake (separate from dropdown)
  bonusManualStakeInput?: string;
  onBonusManualStakeChange?: (value: string) => void;
  onBonusManualStakeSubmit?: (value: string) => void;
  isBonusManualStakeMode?: boolean;
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
  bonusWorkflow,
  bonusOptions = [],
  selectedBonusIndex = 0,
  onBonusSelect,
  onBonusCancel,
  bonusManualStakeInput = '',
  onBonusManualStakeChange,
  onBonusManualStakeSubmit,
  isBonusManualStakeMode = false,
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
  const isBonusActive = bonusWorkflow?.step !== 'idle' && bonusWorkflow?.step !== undefined;
  const isBankrollActive = bankrollWorkflow?.step !== 'idle' && bankrollWorkflow?.step !== undefined;

  // Reset minimized state when workflow changes
  useEffect(() => {
    setIsMinimized(false);
  }, [dropdownWorkflow.type, bonusWorkflow?.step, bankrollWorkflow?.step]);

  // Focus input when entering manual mode (dropdown, bonus, or bankroll)
  useEffect(() => {
    if ((isManualStakeMode || isBonusManualStakeMode || isBankrollAmountMode || isBankrollResetConfirmMode) && manualInputRef.current) {
      manualInputRef.current.focus();
    }
  }, [isManualStakeMode, isBonusManualStakeMode, isBankrollAmountMode, isBankrollResetConfirmMode]);

  // Auto-scroll to keep selected option visible
  const currentSelectedIndex = isDropdownActive
    ? selectedIndex
    : isBonusActive
    ? selectedBonusIndex
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
        case 'extract':
          return 'Extraction';
        case 'arb':
          return 'Arbitrage';
        case 'value':
          return 'Value Bets';
        default:
          return 'Select';
      }
    }
    if (isBonusActive) {
      return 'Bonus Arbitrage';
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
          return dropdownWorkflow.type === 'extract' ? 'Select providers' : 'Select provider';
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
    if (isBonusActive && bonusWorkflow) {
      switch (bonusWorkflow.step) {
        case 'select-provider':
          return 'Select anchor provider';
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
    } else if (isBonusActive && onBonusCancel) {
      onBonusCancel();
    } else if (isBankrollActive && onBankrollCancel) {
      onBankrollCancel();
    }
  };

  // Render nothing if no workflow is active
  if (!isDropdownActive && !isBonusActive && !isBankrollActive) {
    return null;
  }

  // Get options to display
  const options = isDropdownActive
    ? dropdownOptions
    : isBonusActive
    ? bonusOptions
    : bankrollOptions;

  return (
    <div className="border border-terminal-border rounded bg-terminal-surface font-mono text-sm my-2">
      {/* Header - clickable to minimize */}
      <div
        className="flex items-center justify-between px-3 py-2 border-b border-terminal-border cursor-pointer hover:bg-terminal-bg/50"
        onClick={() => setIsMinimized(!isMinimized)}
      >
        <div className="flex items-center gap-2">
          <span className="text-terminal-muted text-xs">
            {isMinimized ? '▸' : '▾'}
          </span>
          <span className="text-terminal-accent text-xs font-medium">
            {getWorkflowLabel()}
          </span>
          <span className="text-terminal-muted text-xs">
            {getStepLabel()} ({options.length})
          </span>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            handleCancel();
          }}
          className="text-terminal-muted hover:text-terminal-text text-xs"
        >
          esc
        </button>
      </div>

      {/* Content - collapsible */}
      {!isMinimized && (
        <div className="p-2 max-h-64 overflow-y-auto">
          {/* Manual stake input mode (dropdown or bonus) */}
          {(isManualStakeMode || isBonusManualStakeMode) ? (
            <div className="flex items-center gap-2">
              <span className="text-terminal-muted">$</span>
              <input
                ref={manualInputRef}
                type="number"
                value={isBonusManualStakeMode ? bonusManualStakeInput : manualStakeInput}
                onChange={(e) => {
                  if (isBonusManualStakeMode) {
                    onBonusManualStakeChange?.(e.target.value);
                  } else {
                    onManualStakeChange?.(e.target.value);
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    if (isBonusManualStakeMode) {
                      onBonusManualStakeSubmit?.(bonusManualStakeInput);
                    } else {
                      onManualStakeSubmit?.(manualStakeInput);
                    }
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    handleCancel();
                  }
                }}
                placeholder="Enter stake amount..."
                className="flex-1 bg-terminal-bg border border-terminal-border rounded px-2 py-1.5 text-terminal-text placeholder-terminal-muted/50 outline-none focus:border-terminal-accent"
              />
              <button
                onClick={() => {
                  if (isBonusManualStakeMode) {
                    onBonusManualStakeSubmit?.(bonusManualStakeInput);
                  } else {
                    onManualStakeSubmit?.(manualStakeInput);
                  }
                }}
                className="px-3 py-1.5 rounded bg-terminal-accent/20 text-terminal-accent hover:bg-terminal-accent/30 transition-colors"
              >
                OK
              </button>
              <button
                onClick={handleCancel}
                className="px-2 py-1.5 rounded text-terminal-muted hover:text-terminal-text hover:bg-terminal-bg/50 transition-colors"
              >
                [cancel]
              </button>
            </div>
          ) : isBankrollAmountMode ? (
            /* Bankroll amount input mode */
            <div className="flex items-center gap-2">
              <span className="text-terminal-muted">$</span>
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
                className="flex-1 bg-terminal-bg border border-terminal-border rounded px-2 py-1.5 text-terminal-text placeholder-terminal-muted/50 outline-none focus:border-terminal-accent"
              />
              <button
                onClick={() => onBankrollAmountSubmit?.()}
                className="px-3 py-1.5 rounded bg-terminal-accent/20 text-terminal-accent hover:bg-terminal-accent/30 transition-colors"
              >
                OK
              </button>
              <button
                onClick={handleCancel}
                className="px-2 py-1.5 rounded text-terminal-muted hover:text-terminal-text hover:bg-terminal-bg/50 transition-colors"
              >
                [cancel]
              </button>
            </div>
          ) : isBankrollResetConfirmMode ? (
            /* Bankroll reset confirmation mode */
            <div className="flex items-center gap-2">
              <span className="text-terminal-muted">Type RESET:</span>
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
                className="flex-1 bg-terminal-bg border border-terminal-border rounded px-2 py-1.5 text-terminal-text placeholder-terminal-muted/50 outline-none focus:border-terminal-accent uppercase"
              />
              <button
                onClick={() => onBankrollConfirmSubmit?.()}
                className="px-3 py-1.5 rounded bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors"
              >
                RESET
              </button>
              <button
                onClick={handleCancel}
                className="px-2 py-1.5 rounded text-terminal-muted hover:text-terminal-text hover:bg-terminal-bg/50 transition-colors"
              >
                [cancel]
              </button>
            </div>
          ) : (
            /* Flex wrap for opportunity numbers, grid for other steps */
            <div className="flex flex-col gap-1">
              {isDropdownActive
                ? dropdownOptions.map((opt, idx) => (
                    <button
                      key={`${opt.id}-${idx}`}
                      ref={(el) => { if (el) optionRefs.current.set(idx, el); }}
                      onClick={() => onDropdownSelect(opt)}
                      className={`text-left px-2 py-1.5 rounded flex items-center gap-2 transition-colors ${
                        idx === currentSelectedIndex
                          ? 'bg-terminal-accent/15 text-terminal-accent'
                          : 'hover:bg-terminal-bg/50 text-terminal-text'
                      }`}
                    >
                      {/* Checkbox for extract multi-select */}
                      {dropdownWorkflow.type === 'extract' && opt.type === 'provider' && (
                        <span
                          className={`w-4 h-4 border rounded flex items-center justify-center text-xs flex-shrink-0 ${
                            opt.selected
                              ? 'bg-terminal-green/20 border-terminal-green text-terminal-green'
                              : 'border-terminal-muted/50'
                          }`}
                        >
                          {opt.selected ? '✓' : ''}
                        </span>
                      )}
                      <span className="font-medium truncate">{opt.label}</span>
                      {opt.sublabel && (
                        <span className="text-xs text-terminal-muted truncate">
                          {opt.sublabel}
                        </span>
                      )}
                    </button>
                  ))
                : isBonusActive
                ? bonusOptions.map((opt, idx) => (
                    <button
                      key={`${opt.id}-${idx}`}
                      ref={(el) => { if (el) optionRefs.current.set(idx, el); }}
                      onClick={() => onBonusSelect?.(opt)}
                      className={`text-left px-2 py-1.5 rounded flex items-center gap-2 transition-colors ${
                        idx === currentSelectedIndex
                          ? 'bg-terminal-accent/15 text-terminal-accent'
                          : 'hover:bg-terminal-bg/50 text-terminal-text'
                      }`}
                    >
                      <span className="font-medium truncate">{opt.label}</span>
                      {opt.sublabel && (
                        <span className="text-xs text-terminal-muted truncate">
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
                          ? 'bg-terminal-accent/15 text-terminal-accent'
                          : 'hover:bg-terminal-bg/50 text-terminal-text'
                      }`}
                    >
                      <span className="font-medium truncate">{opt.label}</span>
                      {opt.sublabel && (
                        <span className="text-xs text-terminal-muted truncate">
                          {opt.sublabel}
                        </span>
                      )}
                    </button>
                  ))}
              {/* Cancel option */}
              <button
                onClick={handleCancel}
                className="text-left px-2 py-1.5 rounded flex items-center gap-2 transition-colors hover:bg-terminal-bg/50 text-terminal-muted hover:text-terminal-text"
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
