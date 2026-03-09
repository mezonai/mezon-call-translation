/**
 * Time range presets for room list filter (Grafana-style).
 * All times are UTC.
 */

export const TIME_RANGE_PRESET = Object.freeze({
  NONE: 'none',
  LAST_12H: '12h',
  LAST_24H: '24h',
  LAST_2D: '2d',
  LAST_7D: '7d',
});

/** Preset options for dropdown */
export const TIME_RANGE_PRESET_OPTIONS = Object.freeze([
  { value: TIME_RANGE_PRESET.NONE, label: 'No time filter' },
  { value: TIME_RANGE_PRESET.LAST_12H, label: 'Last 12 hours' },
  { value: TIME_RANGE_PRESET.LAST_24H, label: 'Last 24 hours' },
  { value: TIME_RANGE_PRESET.LAST_2D, label: 'Last 2 days' },
  { value: TIME_RANGE_PRESET.LAST_7D, label: 'Last 7 days' },
]);

/**
 * Compute from_utc and to_utc for a preset (UTC).
 * to_utc is always now. from_utc is now minus the preset duration.
 * @param {string} preset - One of TIME_RANGE_PRESET values
 * @returns {{ fromUtc: string | null, toUtc: string | null }} ISO strings or null
 */
export function getTimeRangeForPreset(preset) {
  if (!preset || preset === TIME_RANGE_PRESET.NONE) {
    return { fromUtc: null, toUtc: null };
  }
  const now = new Date();
  const toUtc = now.toISOString();
  let fromDate;
  switch (preset) {
    case TIME_RANGE_PRESET.LAST_12H:
      fromDate = new Date(now.getTime() - 12 * 60 * 60 * 1000);
      break;
    case TIME_RANGE_PRESET.LAST_24H:
      fromDate = new Date(now.getTime() - 24 * 60 * 60 * 1000);
      break;
    case TIME_RANGE_PRESET.LAST_2D:
      fromDate = new Date(now.getTime() - 2 * 24 * 60 * 60 * 1000);
      break;
    case TIME_RANGE_PRESET.LAST_7D:
      fromDate = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
      break;
    default:
      return { fromUtc: null, toUtc: null };
  }
  return { fromUtc: fromDate.toISOString(), toUtc: toUtc };
}
