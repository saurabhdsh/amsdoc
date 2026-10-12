import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button, Card, Field, Notice, Page, Pill, QueryState, inputClass, sourceTone } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { can, useAuth } from "../lib/auth";
import { titleCase, when } from "../lib/format";

type Incident = {
  id: string;
  number: string;
  short_description: string;
  description: string;
  state: string;
  priority: string;
  source: string;
  source_label: string;
  live_integration: boolean;
  environment: string;
  external_number: string | null;
  assignment_group: string;
  business_service: string;
  cmdb_ci: string;
  opened_at: string;
  sla_due_at: string | null;
  servicenow_sync_status: string;
  close_notes: string;
  resolution_code: string;
  journal: Array<{ text?: string; element?: string }>;
};

export function Incidents() {
  const user = useAuth((state) => state.user);
  const client = useQueryClient();
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [state, setState] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [link, setLink] = useState("");
  const [message, setMessage] = useState("");
  const list = useQuery({
    queryKey: ["incidents", q, source, state],
    queryFn: () => api<{ total: number; items: Incident[] }>(`/incidents?limit=50&q=${encodeURIComponent(q)}&source=${source}&state=${state}`),
  });
  const detail = useQuery({
    queryKey: ["incident", selected],
    enabled: Boolean(selected),
    queryFn: () => api<Incident>(`/incidents/${selected}`),
  });
  const investigate = useMutation({
    mutationFn: (id: string) => api(`/incidents/${id}/investigate`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["investigation"] }),
  });
  const linkSnow = useMutation({
    mutationFn: () => api(`/incidents/${selected}/link-servicenow`, { method: "POST", body: JSON.stringify({ number: link }) }),
    onSuccess: (result) => {
      setMessage(JSON.stringify(result));
      void client.invalidateQueries({ queryKey: ["incident", selected] });
    },
  });

  return (
    <Page title="Incidents" lede="Normalized records from ServiceNow, the Easidoc simulator, and manual entry. Source is stored with each record." actions={<span className="text-sm text-mist">{list.data?.total ?? 0} records</span>}>
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Search"><input className={inputClass} value={q} onChange={(event) => setQ(event.target.value)} placeholder="Number or description" /></Field>
        <Field label="Source">
          <select className={inputClass} value={source} onChange={(event) => setSource(event.target.value)}>
            <option value="">All sources</option>
            <option value="easidoc_simulator">Easidoc simulator</option>
            <option value="servicenow">ServiceNow</option>
            <option value="manual">Manual</option>
          </select>
        </Field>
        <Field label="State">
          <select className={inputClass} value={state} onChange={(event) => setState(event.target.value)}>
            <option value="">All states</option>
            {["new", "in_progress", "on_hold", "investigating", "awaiting_approval", "resolved", "closed", "canceled", "remediation_failed", "escalated"].map((item) => <option key={item}>{item}</option>)}
          </select>
        </Field>
      </div>
      <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
        <QueryState loading={list.isLoading} error={list.error}>
          <Card className="overflow-hidden p-0">
            <table className="w-full text-left text-sm">
              <thead className="bg-paper text-xs uppercase tracking-wide text-mist">
                <tr>
                  <th className="px-4 py-3">Incident</th>
                  <th className="px-4 py-3">State</th>
                  <th className="px-4 py-3">Source</th>
                </tr>
              </thead>
              <tbody>
                {(list.data?.items ?? []).map((item) => (
                  <tr key={item.id} className="cursor-pointer border-t border-line hover:bg-paper" onClick={() => setSelected(item.id)}>
                    <td className="px-4 py-3">
                      <p className="font-medium">{item.number}</p>
                      <p className="text-mist">{item.short_description}</p>
                    </td>
                    <td className="px-4 py-3"><Pill>{titleCase(item.state)}</Pill></td>
                    <td className="px-4 py-3"><Pill tone={sourceTone(item.source)}>{item.source_label}</Pill></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!list.data?.items.length ? <p className="px-4 py-8 text-sm text-mist">No incidents match these filters.</p> : null}
          </Card>
        </QueryState>
        <Card>
          {detail.data ? (
            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between">
                <h2 className="serif text-2xl">{detail.data.number}</h2>
                <Pill tone={detail.data.live_integration ? "teal" : "amber"}>{detail.data.live_integration ? "Live ServiceNow" : detail.data.source_label}</Pill>
              </div>
              <p>{detail.data.short_description}</p>
              <p className="whitespace-pre-wrap text-mist">{detail.data.description}</p>
              {detail.data.close_notes ? (
                <div className="rounded-xl border border-line bg-paper p-3">
                  <p className="text-xs font-medium uppercase tracking-wide text-mist">Resolution{detail.data.resolution_code ? ` · ${detail.data.resolution_code}` : ""}</p>
                  <p className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap">{detail.data.close_notes}</p>
                </div>
              ) : null}
              <dl className="grid grid-cols-2 gap-2 text-xs">
                <Meta label="Priority" value={detail.data.priority} />
                <Meta label="Opened" value={when(detail.data.opened_at)} />
                <Meta label="Service" value={detail.data.business_service} />
                <Meta label="CI" value={detail.data.cmdb_ci} />
                <Meta label="Group" value={detail.data.assignment_group} />
                <Meta label="ServiceNow sync" value={titleCase(detail.data.servicenow_sync_status)} />
              </dl>
              <div className="flex flex-wrap gap-2">
                <Link className="rounded-full bg-navy px-4 py-2 text-white" to={`/investigation?incident=${detail.data.id}`}>Open investigation</Link>
                {can(user, "incident.investigate") ? <Button kind="quiet" disabled={investigate.isPending} onClick={() => investigate.mutate(detail.data!.id)}>Run investigation</Button> : null}
              </div>
              {can(user, "incident.investigate") ? (
                <div className="space-y-2 border-t border-line pt-3">
                  <Field label="Link a real ServiceNow number">
                    <input className={inputClass} value={link} onChange={(event) => setLink(event.target.value)} placeholder="INC0012345" />
                  </Field>
                  <Button kind="quiet" disabled={!link || linkSnow.isPending} onClick={() => linkSnow.mutate()}>Link</Button>
                  {linkSnow.error ? <Notice tone="error">{errorMessage(linkSnow.error)}</Notice> : null}
                  {message ? <p className="text-xs text-mist">{message}</p> : null}
                </div>
              ) : null}
            </div>
          ) : (
            <p className="text-sm text-mist">Select an incident to inspect its source, SLA, and journal.</p>
          )}
        </Card>
      </div>
    </Page>
  );
}

function Meta({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <dt className="text-mist">{label}</dt>
      <dd className="font-medium">{value || "—"}</dd>
    </div>
  );
}
