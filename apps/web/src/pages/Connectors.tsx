import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Button, Card, Field, Notice, Page, Pill, QueryState, inputClass } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { can, useAuth } from "../lib/auth";

type Connector = { connector: string; status: string; configured?: boolean; message?: string; environment_label?: string; declared_capabilities?: string[]; last_error?: string };
type Snow = Connector & { instance_url: string; username: string; write_mode: string; assignment_group: string; priorities?: string[]; historical_days: number; sync_frequency_seconds: number; auto_sync: boolean; password_set: boolean };

export function Connectors() {
  const user = useAuth((state) => state.user);
  const client = useQueryClient();
  const list = useQuery({ queryKey: ["connectors"], queryFn: () => api<{ items: Connector[] }>("/connectors") });
  const snow = useQuery({ queryKey: ["snow"], queryFn: () => api<Snow>("/connectors/servicenow") });
  const [form, setForm] = useState({ instance_url: "", username: "", password: "", assignment_group: "", write_mode: "read_only", historical_days: 30, sync_frequency_seconds: 300, auto_sync: false, priorities: [] as string[] });
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (!snow.data) return;
    setForm((current) => ({ ...current, instance_url: snow.data.instance_url || "", username: snow.data.username || "", write_mode: snow.data.write_mode, assignment_group: snow.data.assignment_group || "", priorities: snow.data.priorities || [], historical_days: snow.data.historical_days, sync_frequency_seconds: snow.data.sync_frequency_seconds, auto_sync: snow.data.auto_sync }));
  }, [snow.data]);
  const save = useMutation({
    mutationFn: () => api("/connectors/servicenow", { method: "PUT", body: JSON.stringify({ ...form, password: form.password || null }) }),
    onMutate: () => setNotice(""),
    onSuccess: () => {
      setNotice("Configuration saved.");
      setForm((current) => ({ ...current, password: "" }));
      void client.invalidateQueries({ queryKey: ["snow"] });
      void client.invalidateQueries({ queryKey: ["connectors"] });
    },
  });
  const test = useMutation({
    mutationFn: () => api<{ ok?: boolean; message?: string }>("/connectors/servicenow/test", { method: "POST" }),
    onMutate: () => setNotice(""),
    onSuccess: (result) => setNotice(result.ok ? "Connection succeeded." : result.message || "Connection test finished."),
  });
  const sync = useMutation({
    mutationFn: () => api<{ created?: number; updated?: number; knowledge_upserted?: number }>("/connectors/servicenow/sync", { method: "POST" }),
    onMutate: () => setNotice(""),
    onSuccess: (result) => {
      setNotice(`Sync finished. ${result.created ?? 0} incidents created, ${result.updated ?? 0} updated, ${result.knowledge_upserted ?? 0} knowledge articles saved.`);
      void client.invalidateQueries({ queryKey: ["incidents"] });
    },
  });

  return (
    <Page title="Application Connectors" lede="A connector reports not configured until credentials exist. The platform does not invent a successful external response.">
      <QueryState loading={list.isLoading} error={list.error}>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(list.data?.items ?? []).map((item) => (
            <Card key={item.connector}>
              <div className="flex items-center justify-between">
                <h2 className="font-medium capitalize">{item.connector.replaceAll("_", " ")}</h2>
                <Pill tone={item.status === "connected" || item.status === "configured" ? "teal" : "amber"}>{item.status}</Pill>
              </div>
              <p className="mt-2 text-sm text-mist">{item.message || item.environment_label || item.last_error || "No live data is being returned."}</p>
              <p className="mt-2 text-xs text-mist">{(item.declared_capabilities || []).join(" · ")}</p>
            </Card>
          ))}
        </div>
      </QueryState>
      <Card>
        <h2 className="serif text-2xl">ServiceNow</h2>
        <p className="mt-1 text-sm text-mist">Passwords are encrypted at rest and are never returned to the browser. Leave the password blank to keep the stored secret.</p>
        {can(user, "connector.admin") ? (
          <form className="mt-4 grid gap-3 md:grid-cols-2" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
            <Field label="Instance URL"><input className={inputClass} value={form.instance_url} onChange={(event) => setForm({ ...form, instance_url: event.target.value })} placeholder="https://example.service-now.com" /></Field>
            <Field label="Username"><input className={inputClass} value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} /></Field>
            <Field label="Password"><input type="password" className={inputClass} value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder={snow.data?.password_set ? "Stored" : ""} /></Field>
            <Field label="Assignment group filter"><input className={inputClass} value={form.assignment_group} onChange={(event) => setForm({ ...form, assignment_group: event.target.value })} /></Field>
            <Field label="Write mode">
              <select className={inputClass} value={form.write_mode} onChange={(event) => setForm({ ...form, write_mode: event.target.value })}>
                <option value="read_only">Read only</option>
                <option value="approved_write">Approved write</option>
              </select>
            </Field>
            <Field label="Historical days"><input type="number" className={inputClass} value={form.historical_days} onChange={(event) => setForm({ ...form, historical_days: Number(event.target.value) })} /></Field>
            <div className="flex flex-wrap gap-2 md:col-span-2">
              <Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving configuration…" : "Save configuration"}</Button>
              <Button kind="quiet" disabled={test.isPending} onClick={() => test.mutate()}>{test.isPending ? "Testing connection…" : "Test connection"}</Button>
              <Button kind="quiet" disabled={sync.isPending} onClick={() => sync.mutate()}>{sync.isPending ? "Syncing…" : "Sync incidents and knowledge"}</Button>
            </div>
          </form>
        ) : <p className="mt-3 text-sm text-mist">Your role can view connector status. Configuration requires an administrator.</p>}
        {notice ? <div className="mt-3"><Notice tone="ok">{notice}</Notice></div> : null}
        {save.error || test.error || sync.error ? <div className="mt-3"><Notice tone="error">{errorMessage(save.error || test.error || sync.error)}</Notice></div> : null}
      </Card>
    </Page>
  );
}
