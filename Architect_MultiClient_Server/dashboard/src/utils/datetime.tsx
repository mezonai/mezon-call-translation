export const formatDate = (dateString: string, timezone: string = 'vi-VN') => {
    if (!dateString || dateString === 'None') return 'N/A';
    return new Date(dateString).toLocaleString(timezone);
};
