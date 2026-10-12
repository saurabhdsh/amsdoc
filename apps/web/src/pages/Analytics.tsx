import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, Field, Page, QueryState, inputClass } from "../components/ui";
import { api } from "../lib/api";
import { seconds } from "../lib/format";

export function Analytics() {
  const [environment, setEnvironment] = useState("");
  const [source, setSource] = useState("");
  const [priority, setPriority] = useState("");
  const kpis = useQuery({
    queryKey: ["kpis", environment, source, priority],
    queryFn: () => api<Record<string, unknown>>(`/analytics/kpis?environment=${environment}&source=${source}&priority=${priority}`),
  });
  const bySource = Object.entries((kpis.data?.by_source as Record<string, number>) || {}).map(([name, value]) => ({ name, value }));

  return (
    <Page title="Analytics and Outcomes" lede="Every figure is computed from stored incidents, approvals, and verifications. Filter simulated data away from ServiceNow data.">
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Environment">
          <select className={inputClass} value={environment} onChange={(event) => setEnvironment(event.target.value)}>
            <option value="">All</option>
            <option value="simulator">Simulator</option>
            <option value="production">Production</option>
          </select>
        </Field>
        <Field label="Source">
          <select className={inputClass} value={source} onChange={(event) => setSource(event.target.value)}>
            <option value="">All</option>
            <option value="easidoc_simulator">Easidoc simulator</option>
            <option value="servicenow">ServiceNow</option>
            <option value="manual">Manual</option>
          </select>
        </Field>
        <Field label="Priority">
          <select className={inputClass} value={priority} onChange={(event) => setPriority(event.target.value)}>
            <option value="">All</option>
            {["1", "2", "3", "4", "5"].map((item) => <option key={item}>{item}</option>)}
          </select>
        </Field>
      </div>
      <QueryState loading={kpis.isLoading} error={kpis.error}>
        <div className="grid gap-3 md:grid-cols-4">
          {["active_incidents", "sla_exposure", "recommendations", "verified_resolutions", "escalations", "reopen_count"].map((key) => (
            <Card key={key}><p className="text-xs uppercase tracking-wide text-mist">{key.replaceAll("_", " ")}</p><p className="serif mt-2 text-3xl">{String(kpis.data?.[key] ?? "—")}</p></Card>
          ))}
          <Card><p className="text-xs uppercase tracking-wide text-mist">MTTR</p><p className="serif mt-2 text-3xl">{seconds(kpis.data?.mttr_seconds as number | null)}</p></Card>
          <Card><p className="text-xs uppercase tracking-wide text-mist">Investigation coverage</p><p className="serif mt-2 text-3xl">{kpis.data?.investigation_coverage == null ? "—" : `${Math.round(Number(kpis.data.investigation_coverage) * 100)}%`}</p></Card>
        </div>
        <Card>
          <h2 className="text-sm font-medium">Incidents by source</h2>
          <div className="mt-3 h-56">
            <ResponsiveContainer>
              <BarChart data={bySource}>
                <CartesianGrid stroke="#e4e9ef" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#5d6b7c" }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: "#5d6b7c" }} />
                <Tooltip />
                <Bar dataKey="value" fill="#1d1d1f" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </QueryState>
    </Page>
  );
}
