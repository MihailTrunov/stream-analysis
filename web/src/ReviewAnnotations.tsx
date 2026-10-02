import { useEffect, useState } from 'react';
import { apiJson, type ReplayDetectorEvent, type ReviewPattern, type ValidationAnnotation } from './replay';

const REVIEW_LABELS = [
  ['needs_review', 'Needs review'],
  ['correct', 'Correct detection'],
  ['incorrect', 'Incorrect detection'],
  ['partially_correct', 'Partially correct'],
  ['missed_context', 'Missed context'],
] as const;

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function latest(annotation: ValidationAnnotation) {
  return annotation.history.at(-1)!;
}

function ReviewHistory({ annotation }: { annotation: ValidationAnnotation }) {
  return <details><summary>Audit history ({annotation.history.length} revisions)</summary>
    <ol>{annotation.history.map((revision) => <li key={revision.revision}>
      Revision {revision.revision} · {revision.label.replaceAll('_', ' ')} · {revision.changed_at}
      {revision.reviewer_id && <> · reviewer {revision.reviewer_id}</>}
      {revision.note && <> · {revision.note}</>}
    </li>)}</ol>
  </details>;
}

function ReviewExport({ annotation }: { annotation: ValidationAnnotation }) {
  return <a href={`/api/annotations/${annotation.annotation_id}/export`}
    download={`annotation-${annotation.annotation_id}.json`}>Export review JSON</a>;
}

interface ReviewProps {
  runId: string;
  event: ReplayDetectorEvent;
  annotations: ValidationAnnotation[];
  onSaved: (record: ValidationAnnotation) => void;
}

export function EventReview({ runId, event, annotations, onSaved }: ReviewProps) {
  const [targetKind, setTargetKind] = useState<'event' | 'instance'>('event');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [label, setLabel] = useState('needs_review');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const attached = annotations.filter((item) => item.event_id === event.event_id
    || (item.target_kind === 'instance' && item.instance_id === event.instance_id));

  useEffect(() => {
    setTargetKind('event'); setEditingId(null); setLabel('needs_review'); setNote(''); setError(null);
  }, [event.event_id]);

  async function save() {
    setSaving(true); setError(null);
    try {
      const record = editingId
        ? await apiJson<ValidationAnnotation>(`/annotations/${editingId}`, {
            method: 'PATCH', body: JSON.stringify({
              expected_revision: latest(attached.find((item) => item.annotation_id === editingId)!).revision,
              label, note,
            }),
          })
        : await apiJson<ValidationAnnotation>(`/replay/${runId}/annotations`, {
            method: 'POST', body: JSON.stringify({
              target_kind: targetKind,
              ...(targetKind === 'event' ? { event_id: event.event_id } : { instance_id: event.instance_id }),
              label, note,
            }),
          });
      onSaved(record);
      setEditingId(null); setLabel('needs_review'); setNote('');
    } catch (reason) { setError(message(reason)); }
    finally { setSaving(false); }
  }

  function edit(record: ValidationAnnotation) {
    setEditingId(record.annotation_id);
    setTargetKind(record.target_kind === 'instance' ? 'instance' : 'event');
    setLabel(latest(record).label);
    setNote(latest(record).note ?? '');
    setError(null);
  }

  return <section aria-label="Manual validation" className="manual-validation">
    <h4>Manual validation</h4>
    <p>Research judgment about detection, not outcome or profitability. Stored separately from detector evidence.</p>
    {attached.length ? <ul>{attached.map((item) => <li key={item.annotation_id}>
      <strong>{item.target_kind === 'event' ? 'Event' : 'Pattern instance'}: {latest(item).label.replaceAll('_', ' ')}</strong>
      {latest(item).note && <span> · {latest(item).note}</span>}
      <small> · revision {latest(item).revision} · {latest(item).changed_at}</small>
      <button type="button" onClick={() => edit(item)}>Edit review</button>
      <ReviewExport annotation={item} />
      <ReviewHistory annotation={item} />
    </li>)}</ul> : <p>No review recorded for this event or instance.</p>}
    {error && <p role="alert">{error}</p>}
    <div className="form-row">
      <label>Review target <select aria-label="Review target" value={targetKind}
        onChange={(change) => setTargetKind(change.target.value as 'event' | 'instance')}
        disabled={saving || editingId !== null}>
        <option value="event">This event</option><option value="instance">Whole pattern instance</option>
      </select></label>
      <label>Review label <select aria-label="Review label" value={label}
        onChange={(change) => setLabel(change.target.value)} disabled={saving}>
        {REVIEW_LABELS.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
      </select></label>
    </div>
    <label>Review note <textarea aria-label="Review note" value={note}
      onChange={(change) => setNote(change.target.value)} disabled={saving} /></label>
    <div className="form-row">
      <button type="button" onClick={() => void save()} disabled={saving}>
        {editingId ? 'Save review revision' : 'Add review'}
      </button>
      {editingId && <button type="button" onClick={() => {
        setEditingId(null); setLabel('needs_review'); setNote('');
      }}>Cancel edit</button>}
    </div>
  </section>;
}

interface MissedProps {
  runId: string;
  cursorTime: string | null;
  selectedInterval: { start: string; end: string } | null;
  patterns: ReviewPattern[];
  annotations: ValidationAnnotation[];
  onSaved: (record: ValidationAnnotation) => void;
}

export function MissedPatternReview({ runId, cursorTime, selectedInterval, patterns, annotations, onSaved }: MissedProps) {
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [pattern, setPattern] = useState('');
  const [note, setNote] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [label, setLabel] = useState<'missed_pattern' | 'needs_review'>('missed_pattern');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const missed = annotations.filter((item) => item.target_kind === 'missed_pattern');

  useEffect(() => {
    setStart(''); setEnd(''); setPattern(''); setNote(''); setEditingId(null); setError(null);
  }, [runId]);

  useEffect(() => {
    if (selectedInterval) {
      setStart(selectedInterval.start.slice(0, 16));
      setEnd(selectedInterval.end.slice(0, 16));
    }
  }, [selectedInterval]);

  function useCurrentBar() {
    if (!cursorTime) return;
    setStart(cursorTime.slice(0, 16));
    setEnd(new Date(Date.parse(cursorTime) + 60_000).toISOString().slice(0, 16));
  }

  async function save() {
    setSaving(true); setError(null);
    try {
      const selected = patterns.find((item) => `${item.pattern_id}@${item.pattern_version}` === pattern);
      if (!editingId && (!selected || !start || !end)) {
        throw new Error('Choose a pattern and a UTC interval.');
      }
      const record = editingId
        ? await apiJson<ValidationAnnotation>(`/annotations/${editingId}`, {
            method: 'PATCH', body: JSON.stringify({
              expected_revision: latest(missed.find((item) => item.annotation_id === editingId)!).revision,
              label, note,
            }),
          })
        : await apiJson<ValidationAnnotation>(`/replay/${runId}/annotations`, {
            method: 'POST', body: JSON.stringify({
              target_kind: 'missed_pattern', label, note,
              pattern_id: selected!.pattern_id, pattern_version: selected!.pattern_version,
              interval_start: new Date(`${start}:00Z`).toISOString(),
              interval_end: new Date(`${end}:00Z`).toISOString(),
            }),
          });
      onSaved(record);
      setEditingId(null); setNote(''); setLabel('missed_pattern');
    } catch (reason) { setError(message(reason)); }
    finally { setSaving(false); }
  }

  return <section aria-label="Missed pattern review" className="manual-validation">
    <h3>Missed pattern on chart</h3>
    <p>Mark a visible UTC interval where a registered pattern was expected but not detected.
      This is research metadata, not a detector event.</p>
    {missed.length ? <ul>{missed.map((item) => <li key={item.annotation_id}>
      <strong>{item.pattern_id}@{item.pattern_version}</strong> · {item.interval_start} to {item.interval_end}
      {' · '}{latest(item).label.replaceAll('_', ' ')}
      {latest(item).note && <span> · {latest(item).note}</span>}
      <button type="button" onClick={() => {
        setEditingId(item.annotation_id); setLabel(latest(item).label as 'missed_pattern' | 'needs_review');
        setNote(latest(item).note ?? '');
      }}>Edit missed-pattern review</button>
      <ReviewExport annotation={item} />
      <ReviewHistory annotation={item} />
    </li>)}</ul> : <p>No missed-pattern interval recorded in the visible chart.</p>}
    {error && <p role="alert">{error}</p>}
    {!editingId && <>
      <button type="button" onClick={useCurrentBar} disabled={!cursorTime}>Use current bar interval</button>
      <div className="form-row">
        <label>Missed interval start (UTC) <input aria-label="Missed interval start UTC" type="datetime-local"
          value={start} onChange={(change) => setStart(change.target.value)} disabled={saving} /></label>
        <label>Missed interval end (UTC, exclusive) <input aria-label="Missed interval end UTC" type="datetime-local"
          value={end} onChange={(change) => setEnd(change.target.value)} disabled={saving} /></label>
      </div>
      <label>Expected pattern <select aria-label="Expected pattern" value={pattern}
        onChange={(change) => setPattern(change.target.value)} disabled={saving}>
        <option value="">Choose a registered pattern</option>
        {patterns.map((item) => <option key={`${item.pattern_id}@${item.pattern_version}`}
          value={`${item.pattern_id}@${item.pattern_version}`}>{item.name} · {item.pattern_id}@{item.pattern_version}</option>)}
      </select></label>
    </>}
    <label>Missed-pattern status <select aria-label="Missed-pattern status" value={label}
      onChange={(change) => setLabel(change.target.value as 'missed_pattern' | 'needs_review')}
      disabled={saving}>
      <option value="missed_pattern">Missed pattern</option><option value="needs_review">Needs review</option>
    </select></label>
    <label>Missed-pattern note <textarea aria-label="Missed-pattern note" value={note}
      onChange={(change) => setNote(change.target.value)} disabled={saving} /></label>
    <div className="form-row">
      <button type="button" onClick={() => void save()} disabled={saving}>
        {editingId ? 'Save missed-pattern revision' : 'Add missed pattern'}
      </button>
      {editingId && <button type="button" onClick={() => {
        setEditingId(null); setLabel('missed_pattern'); setNote('');
      }}>Cancel edit</button>}
    </div>
  </section>;
}
