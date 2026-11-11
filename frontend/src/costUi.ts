export type CostRow = Record<string, any>;
export const costCurrency = (value: any, currency = "USD") => {
  if (value === null || value === undefined) return "—";
  try {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Number(value));
  } catch {
    return currency + " " + Number(value).toFixed(2);
  }
};
export async function costApi(path: string, options: RequestInit = {}) {
  const response = await fetch("/api/v1" + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (response.status === 401)
    window.dispatchEvent(new Event("acr:unauthorized"));
  const result = await response.json();
  if (!response.ok) throw Error(result.detail?.message || "请求失败，请重试");
  return result;
}
export const costStates: Record<string, string> = {
  SUCCEEDED: "成功",
  NO_DATA: "源暂无费用",
  RUNNING: "采集中",
  QUEUED: "排队中",
  RETRY_WAIT: "等待重试",
  FAILED: "采集失败",
  PENDING: "待校验",
  NOT_COLLECTED: "尚未采集",
};
export function rawCosts(costs: CostRow[] = []) {
  return costs.length
    ? costs
        .map((c) => costCurrency(c.amount, c.currency) + " " + c.currency)
        .join(" / ")
    : "—";
}
