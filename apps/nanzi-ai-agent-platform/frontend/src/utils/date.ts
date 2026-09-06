export const formatDate = (dateStr: string) => {
  if (!dateStr) return "-";
  const date = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - date.getTime();

  // Less than 1 minute
  if (diff < 60000) {
    return "刚刚";
  }
  // Less than 1 hour
  if (diff < 3600000) {
    return `${Math.floor(diff / 60000)} 分钟前`;
  }
  // Less than 1 day
  if (diff < 86400000) {
    return `${Math.floor(diff / 3600000)} 小时前`;
  }
  // Less than 7 days
  if (diff < 604800000) {
    return `${Math.floor(diff / 86400000)} 天前`;
  }

  // Otherwise show date
  return date.toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};
