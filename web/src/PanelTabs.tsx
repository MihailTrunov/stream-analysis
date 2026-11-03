export type Panel = 'patterns' | 'settings';
interface Props { id: string; panel: Panel; count: number; onChange: (panel: Panel) => void }
export function PanelTabs({ id, panel, count, onChange }: Props) {
  return <div className="panel-tabs" role="tablist" aria-label="Analysis panel"
    onKeyDown={(event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'patterns' : event.key === 'End' ? 'settings'
        : panel === 'patterns' ? 'settings' : 'patterns';
      onChange(next); document.getElementById(`${next}-${id}`)?.focus();
    }}>
    {(['patterns', 'settings'] as const).map((value) => <button key={value} type="button" role="tab"
      id={`${value}-${id}`} aria-selected={panel === value} aria-controls={`panel-${id}`}
      tabIndex={panel === value ? 0 : -1} onClick={() => onChange(value)}>
      {value === 'patterns' ? <>Patterns <span>{count}</span></> : 'Detector settings'}
    </button>)}
  </div>;
}
