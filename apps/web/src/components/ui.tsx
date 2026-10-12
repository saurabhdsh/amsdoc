import type { ReactNode, Ref } from "react";
import { errorMessage } from "../lib/api";

export function Page({ title, lede, actions, children }: { title: string; lede?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="mx-auto max-w-[1440px] space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="serif text-[2rem] leading-none tracking-tight text-ink">{title}</h1>
          {lede ? <p className="mt-2 max-w-2xl text-sm leading-6 text-mist">{lede}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </div>
  );
}

export function Card({ children, className = "", ref }: { children: ReactNode; className?: string; ref?: Ref<HTMLElement> }) {
  return <section ref={ref} className={`rounded-2xl border border-line bg-white p-5 shadow-card ${className}`}>{children}</section>;
}

export function Button({
  children,
  onClick,
  kind = "primary",
  type = "button",
  disabled,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "quiet" | "danger" | "teal";
  type?: "button" | "submit";
  disabled?: boolean;
  className?: string;
}) {
  const styles = {
    primary: "bg-[#1d1d1f] text-white hover:bg-black",
    teal: "bg-[#1d1d1f] text-white hover:bg-black",
    quiet: "border border-[#e8e8ed] bg-white text-[#1d1d1f] hover:bg-[#f5f5f7]",
    danger: "bg-[#1d1d1f] text-white hover:bg-black",
  }[kind];
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex items-center justify-center rounded-lg px-4 py-2 text-[13px] font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

export function Pill({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "teal" | "amber" | "rose" | "navy" }) {
  const styles = {
    slate: "border border-[#e8e8ed] bg-[#f5f5f7] text-[#636366]",
    teal: "bg-[#1d1d1f] text-white",
    amber: "border border-[#e8e8ed] bg-white text-[#1d1d1f]",
    rose: "border border-[#e8e8ed] bg-[#f5f5f7] text-[#1d1d1f]",
    navy: "bg-[#1d1d1f] text-white",
  }[tone];
  return <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold capitalize ${styles}`}>{children}</span>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-mist">{label}</span>
      {children}
    </label>
  );
}

export const inputClass = "w-full rounded-lg border border-[#e8e8ed] bg-white px-3 py-2.5 text-[14px] text-[#1d1d1f] outline-none transition focus:border-[#1d1d1f]";

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "ok" | "warn" | "error" }) {
  const styles = {
    info: "border-[#e8e8ed] bg-[#f5f5f7] text-[#1d1d1f]",
    ok: "border-[#1d1d1f] bg-[#f5f5f7] text-[#1d1d1f]",
    warn: "border-[#e8e8ed] bg-white text-[#1d1d1f]",
    error: "border-[#1d1d1f] bg-[#1d1d1f] text-white",
  }[tone];
  return <div className={`rounded-2xl border px-4 py-3 text-sm leading-6 ${styles}`}>{children}</div>;
}

export function QueryState({ loading, error, children }: { loading?: boolean; error?: unknown; children: ReactNode }) {
  if (loading) return <div className="h-28 animate-pulse rounded-2xl bg-white" />;
  if (error) return <Notice tone="error">{errorMessage(error)}</Notice>;
  return <>{children}</>;
}

export function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-line bg-white px-6 py-10 text-center">
      <p className="serif text-xl text-ink">{title}</p>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-mist">{body}</p>
    </div>
  );
}

export function sourceTone(source?: string): "amber" | "teal" | "slate" {
  if (source === "easidoc_simulator") return "amber";
  if (source === "servicenow") return "teal";
  return "slate";
}
