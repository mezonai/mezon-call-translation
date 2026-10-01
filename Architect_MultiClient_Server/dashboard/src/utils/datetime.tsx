export const formatDate = (dateString: string, timezone: string = 'vi-VN') => {
    if (!dateString || dateString === 'None') return 'N/A';
    return new Date(dateString).toLocaleString(timezone);
};

export const formatCallDuration = (
    createdAt: string | null | undefined,
    finalizedAt: string | null | undefined
) => {
    if (!createdAt || !finalizedAt) return '-';
    const start = Date.parse(createdAt);
    const end = Date.parse(finalizedAt);

    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start)
        return '-';

    const totalSeconds = Math.floor((end-start)/1000)
    const hours = Math.floor(totalSeconds/3600)
    const minutes = Math.floor((totalSeconds%3600)/60)
    const seconds = totalSeconds % 60

    if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`
    if (minutes > 0) return `${minutes}m ${seconds}s`
    return `${seconds}s`
};
