export function formatDate(value?: string | null): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function formatLongDate(value?: string | null): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

export function titleize(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function compactJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

export function statusTone(value?: string | null): string {
  const normalized = String(value || "unknown").toLowerCase();
  if (["healthy", "complete", "success", "succeeded", "delivered", "active", "sent"].includes(normalized)) {
    return "good";
  }
  if (["failed", "error", "canceled", "unhealthy"].includes(normalized)) {
    return "bad";
  }
  if (["processing", "pending", "new", "warning", "degraded", "running", "created", "reused", "initializing"].includes(normalized)) {
    return "warn";
  }
  return "neutral";
}
