import { useState } from "react";
import { RefreshCw, AlertCircle } from "lucide-react";
import { costApi, type CostRow } from "./costUi";

const statuses: Record<string, string> = {
  NOT_SYNCED: "未读取",
  QUEUED: "读取中",
  RUNNING: "读取中",
  RETRY_WAIT: "等待重试",
  FAILED: "读取失败",
  CANCELLED: "已取消",
};
const errors: Record<string, string> = {
  TIER_ACCESS_DENIED: "无读取权限",
  TIER_NOT_AVAILABLE: "暂无层级",
  TIER_CREDENTIALS_MISSING: "未配置凭据",
  SP_AUTH_FAILED: "认证失败",
};
const display = (name: string) =>
  name
    .replace(/^Tier[-_ ]+(\d+)$/i, "Tier $1")
    .replace(/^Free[-_ ]?Tier$/i, "Free Tier");
export default function SubscriptionTier({
  subscription,
  onRefresh,
  onError,
}: {
  subscription: CostRow;
  onRefresh: () => void;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const tier = subscription.tier || { state: "NOT_SYNCED" };
  const pending = ["QUEUED", "RUNNING", "RETRY_WAIT"].includes(tier.state);
  const failed = ["FAILED", "RETRY_WAIT"].includes(tier.state) && tier.error;
  const label = tier.name
    ? display(tier.name)
    : errors[tier.error] || statuses[tier.state] || "未读取";
  const title = tier.last_success
    ? "最近读取：" + new Date(tier.last_success).toLocaleString("zh-CN")
    : undefined;
  return (
    <div className="subscription-tier-cell">
      <span className={"badge " + (tier.name ? "blue" : "gray")} title={title}>
        {label}
      </span>
      {failed && tier.name && (
        <span
          className="tier-error"
          title={
            (errors[tier.error] || "层级更新失败") + (title ? "；" + title : "")
          }
          aria-label="层级更新失败"
        >
          <AlertCircle size={14} />
        </span>
      )}
      <button
        type="button"
        className="icon-button"
        aria-label={"刷新层级：" + subscription.name}
        disabled={busy || pending || !subscription.active}
        onClick={async () => {
          setBusy(true);
          try {
            await costApi(
              "/subscriptions/" + subscription.id + "/tier/refresh",
              { method: "POST" },
            );
            onRefresh();
          } catch (error) {
            onError((error as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <RefreshCw size={14} className={busy || pending ? "spin" : ""} />
      </button>
    </div>
  );
}
