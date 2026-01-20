export const formatDateTime = (dateStr) => {
    if (!dateStr) return '';

    // Handle raw time strings "HH:MM" by assuming they are today
    let d;
    if (/^\d{1,2}:\d{2}$/.test(dateStr)) {
        const now = new Date();
        const [hours, minutes] = dateStr.split(':');
        d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hours, minutes);
    } else {
        d = new Date(dateStr);
    }

    if (isNaN(d.getTime())) return dateStr; // Fallback

    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();

    // Force en-GB for consistency
    const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });

    if (isToday) return `Today ${time}`;
    const date = d.toLocaleDateString('en-GB', { month: 'short', day: 'numeric' });
    return `${date} ${time}`;
};
