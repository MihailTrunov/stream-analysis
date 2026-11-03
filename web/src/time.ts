export type DisplayTimezone = 'Europe/London' | 'UTC';

export function dateTimeInput(instant: string, timeZone: DisplayTimezone): string {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(instant));
  const value = (type: string) => parts.find((part) => part.type === type)!.value;
  return `${value('year')}-${value('month')}-${value('day')}T${value('hour')}:${value('minute')}`;
}

export function inputInstant(input: string, timeZone: DisplayTimezone): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(input)) {
    throw new Error('Choose a valid date and time.');
  }
  const base = Date.parse(`${input}:00Z`);
  const offsets = timeZone === 'UTC' ? [0] : [0, 60];
  const candidates = offsets.map((minutes) => new Date(base - minutes * 60_000))
    .filter((date) => Number.isFinite(date.getTime()) && dateTimeInput(date.toISOString(), timeZone) === input);
  if (candidates.length !== 1) {
    throw new Error(candidates.length ? 'This UK time occurs twice when the clocks change. Select UTC to choose the exact instant.'
      : 'This date/time does not exist in the selected timezone.');
  }
  return candidates[0].toISOString();
}

export function displayTime(instant: string, timeZone: DisplayTimezone = 'Europe/London'): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone, day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short',
  }).format(new Date(instant));
}
