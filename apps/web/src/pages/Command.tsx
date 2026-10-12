import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, Notice, Page, Pill, QueryState, sourceTone } from "../components/ui";
import { api } from "../lib/api";
import { seconds, titleCase, when } from "../lib/format";

type Kpis = {
  active_incidents: number;
  by_priority: Record<string, number>;
  sla_exposure: number;
  mttr_seconds: number | null;
  average_investigation_seconds: number | null;
  investigation_coverage: number;
  recommendations: number;
  recommendations_approved: number;
  verified_resolutions: number;
  escalations: number;
  reopen_count: number;
  knowledge_reuse: number;
  agent_success: number;
  application_health: number | null;
  simulated_incidents: number;
  servicenow_incidents: number;
  manual_incidents: number;
  simulated_data_separated: boolean;
};

type Incident = {
  id: string;
  number: string;
  short_description: string;
  state: string;
  priority: string;
  source: string;
  source_label: string;
  opened_at: string;
};

export function Command() {
  const navigate = useNavigate();
  const kpis = useQuery({ queryKey: ["kpis"], queryFn: () => api<Kpis>("/analytics/kpis") });
  const incidents = useQuery({ queryKey: ["incidents", "recent"], queryFn: () => api<{ items: Incident[] }>("/incidents?limit=8") });
  const data = kpis.data;
  const chart = Object.entries(data?.by_priority ?? {}).map(([name, value]) => ({ name: `P${name}`, value }));

  return (
    <Page title="Command Center" lede="Operational measures from persisted incidents. Simulated Easidoc records stay separate from ServiceNow and manual records.">
      <QueryState loading={kpis.isLoading} error={kpis.error}>
        {data?.servicenow_incidents === 0 ? (
          <Notice tone="warn">ServiceNow has no synced incidents in this account. Counts below do not include invented live tickets.</Notice>
        ) : null}
        <div className="grid gap-4 md:grid-cols-4">
          <Metric label="Active incidents" value={data?.active_incidents} />
          <Metric label="SLA exposure" value={data?.sla_exposure} />
          <Metric label="Average investigation" value={seconds(data?.average_investigation_seconds)} />
          <Metric label="MTTR" value={seconds(data?.mttr_seconds)} />
          <Metric label="Investigation coverage" value={percent(data?.investigation_coverage)} />
          <Metric label="Recommendations" value={`${data?.recommendations_approved ?? 0} / ${data?.recommendations ?? 0} approved`} />
          <Metric label="Verified resolutions" value={data?.verified_resolutions} />
          <Metric label="Escalations" value={data?.escalations} />
          <Metric label="Reopen count" value={data?.reopen_count} />
          <Metric label="Knowledge reuse" value={percent(data?.knowledge_reuse)} />
          <Metric label="Agent step success" value={percent(data?.agent_success)} />
          <Metric label="Application health" value={data?.application_health == null ? "—" : percent(data.application_health)} />
        </div>
        <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
          <Card>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-medium">Active incidents by priority</h2>
              <div className="flex gap-2">
                <Pill tone="amber">Simulated {data?.simulated_incidents ?? 0}</Pill>
                <Pill tone="teal">ServiceNow {data?.servicenow_incidents ?? 0}</Pill>
                <Pill>Manual {data?.manual_incidents ?? 0}</Pill>
              </div>
            </div>
            <div className="h-56">
              {chart.length ? (
                <ResponsiveContainer>
                  <BarChart data={chart}>
                    <CartesianGrid stroke="#e4e9ef" vertical={false} />
                    <XAxis dataKey="name" tick={{ fill: "#5d6b7c", fontSize: 12 }} />
                    <YAxis allowDecimals={false} tick={{ fill: "#5d6b7c", fontSize: 12 }} />
                    <Tooltip />
                    <Bar dataKey="value" fill="#1d1d1f" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <p className="pt-16 text-center text-sm text-mist">No active incidents yet. Seed an Easidoc scenario to begin.</p>
              )}
            </div>
          </Card>
          <Card>
            <h2 className="text-sm font-medium">Recent incidents</h2>
            <div className="mt-3 divide-y divide-line">
              {(incidents.data?.items ?? []).map((item) => (
                <button key={item.id} className="flex w-full items-center gap-3 py-3 text-left" onClick={() => navigate(`/investigation?incident=${item.id}`)}>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{item.number} · {item.short_description}</p>
                    <p className="text-xs text-mist">{when(item.opened_at)} · {titleCase(item.state)}</p>
                  </div>
                  <Pill tone={sourceTone(item.source)}>{item.source_label}</Pill>
                </button>
              ))}
              {!incidents.data?.items.length ? <p className="py-8 text-sm text-mist">The incident store is empty.</p> : null}
            </div>
          </Card>
        </div>
      </QueryState>
    </Page>
  );
}

function Metric({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <Card>
      <p className="text-xs uppercase tracking-wide text-mist">{label}</p>
      <p className="serif mt-2 text-3xl">{value ?? "—"}</p>
    </Card>
  );
}

function percent(value?: number | null) {
  if (value == null) return "—";
  return `${Math.round(value * 100)}%`;
}
