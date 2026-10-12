import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, Card, Field, Notice, Pill, QueryState, inputClass } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { can, useAuth } from "../lib/auth";
import { titleCase, when } from "../lib/format";

type Version = { id: string; version_label: string; state: string; title: string; body: string; document_history: string; planned_effective_date: string };
type Workflow = { id: string; kind: string; name: string; status: string; block_code: string | null; row_version: number; tasks: Array<{ name: string; status: string; assigned_participant_id: string | null; assigned_participant_name: string | null }> };
type DocState = {
  environment_label: string;
  document: { id: string; number: string; title: string; status: string; row_version: number; document_type: string };
  versions: Version[];
  workflows: Workflow[];
  eligible_participants: Array<{ id: string; name: string; email: string; function_code: string; has_user: boolean }>;
  missing_fields: string[];
  approval_started: boolean;
  incident_id: string | null;
  timeline: Array<{ id: number; name: string; created_at: string | null; payload: Record<string, unknown> }>;
};
type Scenario = { id: string; name: string; description: string; variants: string[] };

const TABS = ["Library", "Details", "Metadata", "Versions", "Effective", "Draft", "Timeline", "Workflow", "Participants", "Review", "Approval", "Activity", "Logs", "Scenarios"];

export function Easidoc() {
  const user = useAuth((state) => state.user);
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState("Details");
  const [variant, setVariant] = useState("A");
  const [history, setHistory] = useState("");
  const [date, setDate] = useState("");
  const [comment, setComment] = useState("Approved from the Easidoc workspace.");
  const [notice, setNotice] = useState("");
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: () => api<{ environment_label: string; items: Scenario[] }>("/simulator/scenarios") });
  const documents = useQuery({ queryKey: ["documents"], queryFn: () => api<{ environment_label: string; items: Array<{ id: string; number: string; title: string; status: string }> }>("/simulator/documents") });
  const selected = params.get("document") || documents.data?.items[0]?.id || "";
  const detail = useQuery({ queryKey: ["document", selected], enabled: Boolean(selected), queryFn: () => api<DocState>(`/simulator/documents/${selected}`) });
  const logs = useQuery({
    queryKey: ["logs", "document", selected],
    enabled: Boolean(selected),
    queryFn: () => api<{ logs: Array<{ id: string; level: string; message: string; signature: string }> }>(`/logs?connector=easidoc&document_id=${selected}`),
  });
  const investigation = useQuery({
    queryKey: ["investigation", detail.data?.incident_id],
    enabled: Boolean(detail.data?.incident_id),
    queryFn: () => api<{ incident: { number: string; state: string; servicenow_sync_status: string }; hypotheses: Array<{ statement: string; status: string }>; approvals: Array<{ id: string; status: string; payload: { steps?: Array<{ action: string; params?: Record<string, unknown> }>; summary?: string } }>; verifications: Array<{ recovered: boolean; summary: string }> }>(`/incidents/${detail.data?.incident_id}/investigation`),
  });

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["documents"] });
    void client.invalidateQueries({ queryKey: ["document"] });
    void client.invalidateQueries({ queryKey: ["investigation"] });
    void client.invalidateQueries({ queryKey: ["approvals"] });
  };
  const seed = useMutation({
    mutationFn: (scenarioId: string) => api<{ document: DocState | null }>(`/simulator/scenarios/${scenarioId}/seed`, { method: "POST", idempotent: true, body: JSON.stringify({ variant: scenarioId.includes("metadata") ? variant : null }) }),
    onSuccess: (result) => {
      setNotice("");
      if (result.document?.document.id) setParams({ document: result.document.document.id });
      refresh();
    },
  });
  const investigate = useMutation({
    mutationFn: () => api(`/incidents/${detail.data?.incident_id}/investigate`, { method: "POST" }),
    onSuccess: refresh,
  });
  const approval = investigation.data?.approvals?.[0];
  const decide = useMutation({
    mutationFn: (decision: string) => {
      const modifications: Record<string, string> = {};
      if (history.trim()) modifications.document_history = history.trim();
      if (date.trim()) modifications.planned_effective_date = date.trim();
      return api(`/approvals/${approval?.id}/decide`, { method: "POST", idempotent: true, body: JSON.stringify({ decision, comment, modifications: Object.keys(modifications).length ? modifications : null }) });
    },
    onSuccess: refresh,
  });
  const execute = useMutation({
    mutationFn: () => api(`/actions/${approval?.id}/execute`, { method: "POST", idempotent: true }),
    onSuccess: refresh,
  });
  const review = detail.data?.workflows.find((item) => item.kind === "periodic_review");
  const complete = useMutation({
    mutationFn: () => api(`/simulator/workflows/${review?.id}/complete-review`, { method: "POST", idempotent: true, body: JSON.stringify({ expected_version: review?.row_version }) }),
    onSuccess: refresh,
  });
  const start = useMutation({
    mutationFn: () => api(`/simulator/documents/${selected}/start-approval`, { method: "POST", idempotent: true, body: JSON.stringify({ expected_version: detail.data?.document.row_version }) }),
    onSuccess: (result) => { setNotice("Approval workflow started."); refresh(); return result; },
  });

  const draft = detail.data?.versions.find((item) => item.state === "draft");
  const effective = detail.data?.versions.find((item) => item.state === "effective");
  const error = seed.error || investigate.error || decide.error || execute.error || complete.error || start.error;
  const stages = useMemo(() => stageLabels(detail.data), [detail.data]);

  return (
    <div className="mx-auto max-w-[1500px] space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-amber-700">{scenarios.data?.environment_label ?? "EASIDOC — SIMULATED APPLICATION"}</p>
          <h1 className="serif mt-1 text-4xl">Easidoc Digital Twin</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <select className={inputClass + " w-auto"} value={variant} onChange={(event) => setVariant(event.target.value)}>
            <option value="A">Metadata variant A · history</option>
            <option value="B">Metadata variant B · date</option>
            <option value="C">Metadata variant C · both</option>
          </select>
          <Button kind="quiet" onClick={() => seed.mutate("missing-periodic-review-participant")}>Seed missing participant</Button>
          <Button kind="quiet" onClick={() => seed.mutate("missing-document-metadata")}>Seed missing metadata</Button>
        </div>
      </div>
      {error ? <Notice tone="error">{errorMessage(error)}</Notice> : null}
      {notice ? <Notice>{notice}</Notice> : null}
      <div className="flex flex-wrap gap-2">
        {stages.map((item) => <Pill key={item.label} tone={item.done ? "teal" : "slate"}>{item.label}</Pill>)}
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <Card>
          <div className="flex gap-2 overflow-auto pb-3">
            {TABS.map((item) => (
              <button key={item} className={`shrink-0 rounded-full px-3 py-1.5 text-xs ${tab === item ? "bg-navy text-white" : "bg-paper text-mist"}`} onClick={() => setTab(item)}>{item}</button>
            ))}
          </div>
          <QueryState loading={detail.isLoading || documents.isLoading} error={detail.error}>
            {tab === "Library" ? (
              <div className="divide-y divide-line">
                {(documents.data?.items ?? []).map((item) => (
                  <button key={item.id} className="flex w-full items-center justify-between py-3 text-left" onClick={() => setParams({ document: item.id })}>
                    <span><span className="font-medium">{item.number}</span><span className="block text-sm text-mist">{item.title}</span></span>
                    <Pill>{item.status}</Pill>
                  </button>
                ))}
                {!documents.data?.items.length ? <p className="text-sm text-mist">Seed a scenario to create a simulated document.</p> : null}
              </div>
            ) : null}
            {tab === "Details" && detail.data ? <Detail state={detail.data} /> : null}
            {tab === "Metadata" && draft ? <Metadata version={draft} missing={detail.data?.missing_fields ?? []} /> : null}
            {tab === "Versions" ? detail.data?.versions.map((item) => <VersionCard key={item.id} version={item} />) : null}
            {tab === "Effective" && effective ? <VersionCard version={effective} /> : null}
            {tab === "Draft" && draft ? <VersionCard version={draft} /> : null}
            {tab === "Timeline" || tab === "Activity" ? <Timeline items={detail.data?.timeline ?? []} /> : null}
            {tab === "Workflow" || tab === "Review" || tab === "Approval" ? <Workflows items={detail.data?.workflows ?? []} /> : null}
            {tab === "Participants" ? <Participants items={detail.data?.eligible_participants ?? []} /> : null}
            {tab === "Logs" ? (logs.data?.logs ?? []).map((row) => <p key={row.id} className="border-b border-line py-2 text-sm"><span className="text-xs text-mist">{row.level} {row.signature}</span><br />{row.message}</p>) : null}
            {tab === "Scenarios" ? (scenarios.data?.items ?? []).map((item) => (
              <div key={item.id} className="border-b border-line py-3">
                <p className="font-medium">{item.name}</p>
                <p className="text-sm text-mist">{item.description}</p>
              </div>
            )) : null}
          </QueryState>
          {review && can(user, "easidoc.review.complete") ? (
            <div className="mt-4">
              <Button kind="teal" disabled={complete.isPending || review.status === "completed"} onClick={() => complete.mutate()}>Complete periodic review as assigned participant</Button>
              <p className="mt-2 text-xs text-mist">Only the assigned participant’s account can complete this task. Assignment alone does not finish the review.</p>
            </div>
          ) : null}
          {can(user, "easidoc.approval.start") && detail.data ? (
            <div className="mt-3"><Button kind="quiet" onClick={() => { setNotice(""); start.mutate(); }}>Attempt approval start</Button></div>
          ) : null}
        </Card>
        <Card className="space-y-3 text-sm">
          <p className="text-xs uppercase tracking-wide text-mist">AMS investigation</p>
          {investigation.data ? (
            <>
              <div className="flex items-center justify-between">
                <h2 className="serif text-2xl">{investigation.data.incident.number}</h2>
                <Pill>{titleCase(investigation.data.incident.state)}</Pill>
              </div>
              <p className="text-xs text-mist">ServiceNow sync: {titleCase(investigation.data.incident.servicenow_sync_status)}</p>
              {investigation.data.hypotheses.map((item, index) => <p key={index}>{item.status}: {item.statement}</p>)}
              {approval ? <p>{approval.payload.summary}</p> : <p className="text-mist">No recommendation yet.</p>}
              {(detail.data?.missing_fields ?? []).includes("document_history") ? <Field label="Confirmed document history"><textarea className={inputClass} value={history} onChange={(event) => setHistory(event.target.value)} /></Field> : null}
              {(detail.data?.missing_fields ?? []).includes("planned_effective_date") ? <Field label="Confirmed planned effective date"><input className={inputClass} placeholder="YYYY-MM-DD" value={date} onChange={(event) => setDate(event.target.value)} /></Field> : null}
              {approval?.status === "pending" && can(user, "approval.decide") ? (
                <>
                  <Field label="Approval comment"><input className={inputClass} value={comment} onChange={(event) => setComment(event.target.value)} /></Field>
                  <div className="flex gap-2"><Button kind="teal" onClick={() => decide.mutate("approve")}>Approve</Button><Button kind="danger" onClick={() => decide.mutate("reject")}>Reject</Button></div>
                </>
              ) : null}
              {approval?.status === "approved" && can(user, "action.execute") ? <Button onClick={() => execute.mutate()}>Execute approved action</Button> : null}
              {investigation.data.verifications.at(-1) ? <p>Verification: {investigation.data.verifications.at(-1)?.summary}</p> : null}
            </>
          ) : <p className="text-mist">The simulator creates an incident when a scenario is seeded.</p>}
          {detail.data?.incident_id && can(user, "incident.investigate") ? <Button kind="quiet" onClick={() => investigate.mutate()}>Investigate</Button> : null}
        </Card>
      </div>
    </div>
  );
}

function stageLabels(state?: DocState) {
  const review = state?.workflows.find((item) => item.kind === "periodic_review");
  const missing = (state?.missing_fields.length ?? 0) > 0;
  return [
    { label: "Incident detected", done: Boolean(state?.incident_id) },
    { label: missing ? "Metadata missing" : "Metadata present", done: true },
    { label: review?.block_code === "REVIEW_PARTICIPANT_NOT_FOUND" ? "Participant missing" : "Participant recorded", done: review?.block_code !== "REVIEW_PARTICIPANT_NOT_FOUND" },
    { label: "Review completed", done: review?.status === "completed" || !review },
    { label: "Approval started", done: Boolean(state?.approval_started) },
  ];
}

function Detail({ state }: { state: DocState }) {
  return (
    <div>
      <h2 className="serif text-3xl">{state.document.number}</h2>
      <p className="mt-1">{state.document.title}</p>
      <p className="mt-2 text-sm text-mist">{state.document.document_type} · {state.document.status} · version {state.document.row_version}</p>
      <p className="mt-3 text-sm text-amber-800">{state.environment_label}</p>
    </div>
  );
}

function Metadata({ version, missing }: { version: Version; missing: string[] }) {
  return (
    <div className="space-y-2 text-sm">
      <p>Document history: {version.document_history || "blank"}</p>
      <p>Planned effective date: {version.planned_effective_date || "blank"}</p>
      <p className="text-mist">Missing: {missing.join(", ") || "none"}</p>
    </div>
  );
}

function VersionCard({ version }: { version: Version }) {
  return (
    <article className="border-b border-line py-3">
      <p className="font-medium">{version.version_label} · {version.state}</p>
      <p className="mt-1 text-sm">{version.body}</p>
      <p className="mt-1 text-xs text-mist">History: {version.document_history || "blank"} · Effective {version.planned_effective_date || "blank"}</p>
    </article>
  );
}

function Timeline({ items }: { items: DocState["timeline"] }) {
  return (
    <ol className="space-y-2">
      {items.map((item) => <li key={item.id} className="text-sm"><span className="font-medium">{item.name}</span> <span className="text-mist">{when(item.created_at)}</span></li>)}
    </ol>
  );
}

function Workflows({ items }: { items: Workflow[] }) {
  return (
    <div className="space-y-3">
      {items.map((item) => (
        <div key={item.id} className="rounded-xl bg-paper p-3 text-sm">
          <div className="flex justify-between"><span className="font-medium">{item.name}</span><Pill>{item.status}</Pill></div>
          <p className="mt-1 text-mist">{item.block_code || "No block"}</p>
          {item.tasks.map((task) => <p key={task.name} className="mt-1">{task.name}: {task.status} · {task.assigned_participant_name || "unassigned"}</p>)}
        </div>
      ))}
    </div>
  );
}

function Participants({ items }: { items: DocState["eligible_participants"] }) {
  return (
    <div className="space-y-2 text-sm">
      {items.map((item) => <p key={item.id}>{item.name} · {item.function_code} · {item.has_user ? "can sign in" : "no user account"}</p>)}
      {!items.length ? <p className="text-mist">Eligible participants appear when a review workflow requires a function.</p> : null}
    </div>
  );
}
