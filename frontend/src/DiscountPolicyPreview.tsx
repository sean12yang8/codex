import { AlertCircle, Info } from "lucide-react";
import {
  previewDiscount,
  type DiscountUse,
  type DiscountVersion,
} from "./discountPolicy";
const show = (use: DiscountUse) =>
  use.factor === null
    ? "缺少折扣"
    : `${use.factor}（${Number((Number(use.factor) * 100).toFixed(10))}%）`;
const basis = (use: DiscountUse) =>
  use.mode === "historical"
    ? `${use.versionDay} 起的历史版本`
    : use.mode === "fallback"
      ? `当前版本 ${use.versionDay}`
      : "无历史版本且无当前可回推版本";
export default function DiscountPolicyPreview({
  versions,
  factor,
  effective,
  start,
  end,
}: {
  versions: DiscountVersion[];
  factor: string;
  effective: string;
  start: string;
  end: string;
}) {
  const impact = previewDiscount(versions, factor, effective, start, end);
  return (
    <section
      className="discount-impact"
      aria-label="折扣计算口径预览"
      aria-live="polite"
    >
      <h4>
        <Info size={16} />
        计算口径预览
      </h4>

      {impact.conflict ? (
        <div className="discount-warning" role="alert">
          <AlertCircle size={16} />
          <span>
            {effective} 已有系数 {impact.conflict.factor}{" "}
            的版本。这里是新增版本，不会覆盖同日记录，请选择其他生效日。
          </span>
        </div>
      ) : impact.inputValid ? (
        <div className="discount-effective">
          本次新增：
          <strong>
            {effective} 起使用 {factor}
          </strong>
          {impact.until
            ? `，至 ${impact.until}；之后继续采用已有的更晚版本。`
            : "，直到下一个更晚版本生效。"}
        </div>
      ) : (
        <p>填写系数和生效日期后显示影响范围。</p>
      )}
      <div className="discount-period">
        <strong>
          当前查看区间：{start} — {end}（UTC）
        </strong>
        {impact.valid && impact.rangeValid && (
          <span>{impact.changedDays} 个日期口径会变化</span>
        )}
      </div>
      {impact.valid && impact.otherFactors && (
        <div className="discount-warning">
          <AlertCircle size={16} />
          <span>
            当前区间仍有日期采用其他系数，保存后不会全部按 {factor}{" "}
            核算。具体以以下分段为准。
          </span>
        </div>
      )}
      {impact.rangeValid ? (
        <div className="table-scroll discount-policy-table">
          <table>
            <thead>
              <tr>
                <th>UTC 消费日期</th>
                <th>保存前</th>
                <th>保存后预览</th>
              </tr>
            </thead>
            <tbody>
              {impact.rows.map((row) => (
                <tr key={row.from}>
                  <td>
                    {row.from}
                    {row.from !== row.to && <small>至 {row.to}</small>}
                  </td>
                  <td>
                    <strong>{show(row.before)}</strong>
                    <small>{basis(row.before)}</small>
                  </td>
                  <td>
                    <strong className={row.changed ? "discount-changed" : ""}>
                      {show(row.after)}
                    </strong>
                    <small>{basis(row.after)}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>当前没有可预览的完整 UTC 日期区间。</p>
      )}

      {versions.length > 0 && (
        <details className="discount-history" open>
          <summary>已有折扣版本（{versions.length} 个）</summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>生效日期（UTC）</th>
                  <th>系数</th>
                </tr>
              </thead>
              <tbody>
                {[...versions]
                  .sort((a, b) => b.day.localeCompare(a.day))
                  .map((v) => (
                    <tr key={v.day}>
                      <td>{v.day}</td>
                      <td>
                        {v.factor}（
                        {Number((Number(v.factor) * 100).toFixed(10))}%）
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}
