/**
 * Room status enum and metadata for API values, labels, and badge styling.
 */

export const ROOM_STATUS = Object.freeze({
  PENDING: 'pending',
  COMPLETED: 'completed',
  FINAL_ROOM: 'final_room',
  FAILED: 'failed',
});

/** @type {Record<string, string>} */
export const ROOM_STATUS_LABELS = Object.freeze({
  [ROOM_STATUS.PENDING]: 'Pending',
  [ROOM_STATUS.COMPLETED]: 'Completed',
  [ROOM_STATUS.FINAL_ROOM]: 'Final Room',
  [ROOM_STATUS.FAILED]: 'Failed',
});

/** @type {Record<string, string>} */
export const ROOM_STATUS_BADGE_COLORS = Object.freeze({
  [ROOM_STATUS.PENDING]: 'bg-gray-100 text-gray-800',
  [ROOM_STATUS.COMPLETED]: 'bg-green-100 text-green-800',
  [ROOM_STATUS.FINAL_ROOM]: 'bg-yellow-100 text-yellow-800',
  [ROOM_STATUS.FAILED]: 'bg-red-100 text-red-800',
});

/** Options for status filter dropdown: All + each status */
export const ROOM_STATUS_FILTER_OPTIONS = Object.freeze([
  { value: '', label: 'All statuses' },
  ...Object.entries(ROOM_STATUS_LABELS).map(([value, label]) => ({ value, label })),
]);
