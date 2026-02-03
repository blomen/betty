import { createContext, useContext, ReactNode } from 'react';
import type {
  BonusWorkflowState,
  BonusDropdownOption,
  DropdownWorkflowState,
  DropdownOption,
} from '@/types';

interface WorkflowContextValue {
  // Dropdown workflow (extract, arb, value, bets)
  dropdownWorkflow: DropdownWorkflowState;
  dropdownOptions: DropdownOption[];
  selectedDropdownIndex: number;
  setSelectedDropdownIndex: (idx: number) => void;
  onDropdownSelect: (option: DropdownOption) => void;
  onDropdownCancel: () => void;

  // Bonus workflow
  bonusWorkflow: BonusWorkflowState;
  bonusOptions: BonusDropdownOption[];
  selectedBonusIndex: number;
  setSelectedBonusIndex: (idx: number) => void;
  onBonusSelect: (option: BonusDropdownOption) => void;
  onBonusCancel: () => void;
}

const WorkflowContext = createContext<WorkflowContextValue | null>(null);

interface WorkflowProviderProps {
  children: ReactNode;
  value: WorkflowContextValue;
}

export function WorkflowProvider({ children, value }: WorkflowProviderProps) {
  return (
    <WorkflowContext.Provider value={value}>
      {children}
    </WorkflowContext.Provider>
  );
}

export function useWorkflow() {
  const context = useContext(WorkflowContext);
  if (!context) {
    throw new Error('useWorkflow must be used within a WorkflowProvider');
  }
  return context;
}

export function useWorkflowOptional() {
  return useContext(WorkflowContext);
}
