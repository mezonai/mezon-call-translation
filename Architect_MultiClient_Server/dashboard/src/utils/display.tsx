import React from 'react';
import { ROOM_STATUS_LABELS, ROOM_STATUS_BADGE_COLORS } from '../constants/roomStatus';

export const convertStatusToText = (status: string) => {
    const key = status?.toLowerCase();
    return ROOM_STATUS_LABELS[key] ?? 'Unknown';
};

export const getStatusBadge = (status: string) => {
    const key = status?.toLowerCase();
    const colorClass = ROOM_STATUS_BADGE_COLORS[key] ?? ROOM_STATUS_BADGE_COLORS.pending;

    return (
      <span className={`px-2 py-1 text-xs font-medium rounded-full ${colorClass}`}>
        {convertStatusToText(status)}
      </span>
    );
};