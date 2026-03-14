export function formatNumber(value, digits = 4) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "-";
  }
  return num.toFixed(digits);
}

export function formatCompact(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "-";
  }
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(num);
}

export function normalizeIndicatorSeries(key, history, categoryMaps) {
  return history.map((point) => {
    const raw = point && point.values ? point.values[key] : null;
    return toNumeric(key, raw, categoryMaps);
  });
}

export function toNumeric(key, value, categoryMaps) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "boolean") {
    return value ? 1 : 0;
  }
  if (value === null || value === undefined) {
    return 0;
  }

  const asNumber = Number(value);
  if (Number.isFinite(asNumber)) {
    return asNumber;
  }

  const token = String(value);
  if (!categoryMaps[key]) {
    categoryMaps[key] = {};
  }
  const map = categoryMaps[key];
  if (map[token] === undefined) {
    map[token] = Object.keys(map).length;
  }
  return map[token];
}
