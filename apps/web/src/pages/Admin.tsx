import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, Field, Notice, Page, QueryState, inputClass } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { when } from "../lib/format";

type Audit = { id: string; action: string; resource_type: string; created_at: string; details: Record<string, unknown> };
type AccountUser = { id: string; email: string; name: string; role: string };

export function Admin() {
  const client = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<{ items: AccountUser[] }>("/admin/users") });
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api<{ items: Audit[] }>("/admin/audit") });
  const retention = useQuery({ queryKey: ["retention"], queryFn: () => api<{ days: number }>("/admin/retention") });
  const [form, setForm] = useState({ email: "", name: "", password: "", role: "viewer" });
  const [days, setDays] = useState(90);
  const create = useMutation({
    mutationFn: () => api("/admin/users", { method: "POST", body: JSON.stringify(form) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["users"] }),
  });
  const saveDays = useMutation({
    mutationFn: () => api("/admin/retention", { method: "PUT", body: JSON.stringify({ days }) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["retention"] }),
  });
  const run = useMutation({ mutationFn: () => api("/admin/retention/run", { method: "POST" }) });

  return (
    <Page title="Administration and Settings" lede="Users, audit history, and log retention. Authentication is local in this environment and OIDC-ready when an issuer is configured.">
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="text-sm font-medium">Directory</h2>
          <QueryState error={users.error}>
            <div className="mt-3 divide-y divide-line text-sm">
              {(users.data?.items ?? []).map((item) => <div key={item.id} className="flex justify-between py-2"><span>{item.name}<span className="block text-xs text-mist">{item.email}</span></span><span className="capitalize text-mist">{item.role}</span></div>)}
            </div>
            <form className="mt-4 grid gap-2" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
              <Field label="Name"><input className={inputClass} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></Field>
              <Field label="Email"><input className={inputClass} value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></Field>
              <Field label="Password"><input type="password" className={inputClass} value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} /></Field>
              <Field label="Role">
                <select className={inputClass} value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}>
                  {["admin", "resolver", "investigator", "operator", "viewer"].map((role) => <option key={role}>{role}</option>)}
                </select>
              </Field>
              <Button type="submit">Create user</Button>
            </form>
          </QueryState>
          {create.error ? <div className="mt-3"><Notice tone="error">{errorMessage(create.error)}</Notice></div> : null}
        </Card>
        <Card>
          <h2 className="text-sm font-medium">Retention</h2>
          <p className="mt-2 text-sm text-mist">Current window: {retention.data?.days ?? "—"} days. Running retention deletes old simulator logs only.</p>
          <div className="mt-3 flex gap-2">
            <input type="number" className={inputClass} value={days} onChange={(event) => setDays(Number(event.target.value))} />
            <Button kind="quiet" onClick={() => saveDays.mutate()}>Save</Button>
            <Button kind="quiet" onClick={() => run.mutate()}>Run</Button>
          </div>
          {retention.error || saveDays.error || run.error ? <div className="mt-3"><Notice tone="error">{errorMessage(retention.error || saveDays.error || run.error)}</Notice></div> : null}
          {run.data ? <pre className="mt-3 text-xs">{JSON.stringify(run.data)}</pre> : null}
        </Card>
      </div>
      <Card>
        <h2 className="text-sm font-medium">Audit</h2>
        <QueryState loading={audit.isLoading} error={audit.error}>
          <div className="mt-3 max-h-96 divide-y divide-line overflow-auto text-sm">
            {(audit.data?.items ?? []).map((item) => (
              <div key={item.id} className="py-2">
                <p className="font-medium">{item.action}</p>
                <p className="text-xs text-mist">{when(item.created_at)} · {item.resource_type}</p>
              </div>
            ))}
          </div>
        </QueryState>
      </Card>
    </Page>
  );
}
