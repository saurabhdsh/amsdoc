import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Background, Controls, ReactFlow } from "@xyflow/react";
import { useEffect, useMemo, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import "@xyflow/react/dist/style.css";
import { Button, Card, Empty, Notice, Page, Pill, QueryState } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { titleCase } from "../lib/format";

type RecommendationPayload = {
  disposition?: string;
  summary?: string;
  expected_outcome?: string;
  verification_plan?: string[];
  guidance?: string[];
  resolution_notes?: string;
  closes_incident?: boolean;
  recommendation_version?: number;
  model?: { used?: boolean; provider?: string | null; model?: string | null; message?: string };
  logs?: {
    source?: string;
    searched?: string[];
    matched?: boolean;
    message?: string;
    lines?: Array<{ timestamp?: string; level?: string; source?: string; message?: string; signature?: string }>;
    activity?: Array<{ at?: string; kind?: string; text?: string }>;
  };
};

type InvestigationPayload = {
  incident: { id: string; number: string; short_description: string; state: string; source_label: string };
  runs: Array<{ id: string; status: string; orchestrator: string; error: string | null }>;
  steps: Array<{ id: string; run_id: string; agent: string; status: string; output: Record<string, unknown> }>;
  evidence: Array<{ id: string; run_id: string | null; kind: string; title: string; excerpt: string; source: string }>;
  hypotheses: Array<{ id: string; run_id: string | null; statement: string; status: string; confidence: number; supporting: string[]; contradicting: string[] }>;
  proposals: Array<{ id: string; run_id: string | null; summary: string; risk: string; status: string; payload: RecommendationPayload }>;
  verifications: Array<{ id: string; recovered: boolean; summary: string }>;
};

export function Investigation() {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const started = useRef(new Set<string>());
  const incidents = useQuery({ queryKey: ["incidents", "picker"], queryFn: () => api<{ items: Array<{ id: string; number: string; short_description: string }> }>("/incidents?source=servicenow&limit=50") });
  const incidentId = params.get("incident") || incidents.data?.items[0]?.id || "";
  const investigation = useQuery({
    queryKey: ["investigation", incidentId],
    enabled: Boolean(incidentId),
    queryFn: () => api<InvestigationPayload>(`/incidents/${incidentId}/investigation`),
  });
  const run = useMutation({
    mutationFn: (id: string) => api(`/incidents/${id}/investigate`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["investigation"] }),
  });
  const latestRunId = investigation.data?.runs.at(-1)?.id;
  const recommendation = (investigation.data?.proposals ?? []).find((item) => item.run_id === latestRunId);
  useEffect(() => {
    if (!incidentId || !investigation.data || run.isPending) return;
    const needsRun = investigation.data.steps.length === 0;
    const currentRecommendation = recommendation?.payload?.recommendation_version === 4;
    const needsRecommendation = investigation.data.steps.length > 0 && !currentRecommendation;
    const key = needsRecommendation ? `${incidentId}:recommendation-v4` : incidentId;
    if (!needsRun && !needsRecommendation) return;
    if (started.current.has(key)) return;
    started.current.add(key);
    run.mutate(incidentId);
  }, [incidentId, investigation.data, recommendation?.payload?.recommendation_version, run]);
  const steps = (investigation.data?.steps ?? []).filter((step) => !latestRunId || step.run_id === latestRunId);
  const hypotheses = (investigation.data?.hypotheses ?? []).filter((item) => !latestRunId || item.run_id === latestRunId);
  const evidence = (investigation.data?.evidence ?? []).filter((item) => !latestRunId || item.run_id === latestRunId);
  const nodes = useMemo(
    () => steps.map((step, index) => ({ id: step.id, position: { x: 80 + (index % 2) * 280, y: 20 + index * 88 }, data: { label: `${titleCase(step.agent)} · ${step.status}` }, style: { border: "1px solid #e4e9ef", borderRadius: 16, width: 220, fontSize: 12 } })),
    [steps],
  );
  const edges = useMemo(
    () => steps.slice(1).map((step, index) => ({ id: `${steps[index].id}-${step.id}`, source: steps[index].id, target: step.id })),
    [steps],
  );

  return (
    <Page title="AI Investigation Studio" lede="The graph shows how the incident was investigated. The recommendation says how to resolve it. Closing it stays a human decision.">
      <label className="block max-w-xl text-sm">
        <span className="mb-1 block text-xs uppercase tracking-wide text-mist">Incident</span>
        <select className="w-full rounded-xl border border-line bg-white px-3 py-2" value={incidentId} onChange={(event) => setParams({ incident: event.target.value })}>
          {(incidents.data?.items ?? []).map((item) => <option key={item.id} value={item.id}>{item.number} — {item.short_description}</option>)}
        </select>
      </label>
      <QueryState loading={investigation.isLoading} error={investigation.error}>
        {!incidentId ? <Empty title="No incident selected" body="Seed an Easidoc scenario or sync ServiceNow, then return here." /> : null}
        {investigation.data ? (
          <div className="grid items-start gap-4 xl:grid-cols-[1.1fr_0.9fr]">
            <div className="space-y-4">
              <Card className="h-[420px] p-3">
                {nodes.length ? (
                  <ReactFlow nodes={nodes} edges={edges} fitView preventScrolling={false} zoomOnScroll={false} panOnScroll={false}>
                    <Background />
                    <Controls />
                  </ReactFlow>
                ) : (
                  <div className="grid h-full place-items-center">
                    <div className="max-w-md space-y-4 text-center">
                      <Empty
                        title={run.isPending ? "Running investigation" : "Investigation has not run"}
                        body={run.isPending ? "Intake, knowledge, and root-cause steps are being recorded for this incident." : "Run it here. The graph is drawn from the saved agent steps."}
                      />
                      {run.error ? <Notice tone="error">{errorMessage(run.error)}</Notice> : null}
                      <Button
                        disabled={run.isPending}
                        onClick={() => {
                          started.current.delete(incidentId);
                          run.mutate(incidentId);
                        }}
                      >
                        {run.isPending ? "Running…" : "Run investigation"}
                      </Button>
                    </div>
                  </div>
                )}
              </Card>
              <WorkNotePanel logs={recommendation?.payload.logs} incidentId={incidentId} />
            </div>
            <div className="space-y-4">
              <Card>
                <div className="flex items-center justify-between">
                  <h2 className="font-medium">{investigation.data.incident.number}</h2>
                  <Pill>{titleCase(investigation.data.incident.state)}</Pill>
                </div>
                <p className="mt-2 text-sm text-mist">{investigation.data.incident.short_description}</p>
                <p className="mt-2 text-xs text-mist">{investigation.data.runs.at(-1)?.orchestrator ?? "No run"} · {investigation.data.incident.source_label}</p>
              </Card>
              {run.error ? <Notice tone="error">{errorMessage(run.error)}</Notice> : null}
              <Recommendation key={incidentId} proposal={recommendation} pending={run.isPending && recommendation?.payload?.recommendation_version !== 4} incidentId={incidentId} />
              {hypotheses.map((item) => (
                <Card key={item.id}>
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm">{item.statement}</p>
                    <Pill tone={item.status === "confirmed" ? "teal" : item.status === "rejected" ? "rose" : "amber"}>{item.status}</Pill>
                  </div>
                  <p className="mt-2 text-xs text-mist">Support: {(item.supporting || []).join(" · ") || "none recorded"}</p>
                </Card>
              ))}
              {evidence.filter((item) => item.kind !== "activity").map((item) => (
                <Card key={item.id}>
                  <p className="text-xs uppercase tracking-wide text-mist">{item.kind} · {item.source}</p>
                  <p className="mt-1 text-sm font-medium">{item.title}</p>
                  <p className="mt-1 text-sm text-mist">{item.excerpt}</p>
                </Card>
              ))}
            </div>
          </div>
        ) : null}
      </QueryState>
    </Page>
  );
}

function Recommendation({ proposal, pending, incidentId }: { proposal?: InvestigationPayload["proposals"][number]; pending: boolean; incidentId: string }) {
  const payload = proposal?.payload;
  if (pending || !payload?.disposition) {
    return (
      <Card>
        <h2 className="font-medium">Recommendation</h2>
        <p className="mt-2 text-sm text-mist">{pending ? "Asking the model how to resolve this incident." : "The investigation has not produced a recommendation yet."}</p>
      </Card>
    );
  }
  const label = payload.disposition === "needs_approval" ? "Needs approval" : payload.disposition === "advisory" ? "Advisory" : "Not enough to close";
  const tone = payload.disposition === "needs_approval" ? "navy" : payload.disposition === "advisory" ? "amber" : "rose";
  const guidance = payload.guidance ?? [];
  const checks = payload.verification_plan ?? [];
  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-medium">Recommendation</h2>
        <Pill tone={tone}>{label}</Pill>
      </div>
      <p className="mt-3 text-sm leading-6">{payload.summary || proposal?.summary}</p>
      <LogBrief logs={payload.logs} incidentId={incidentId} />
      {payload.resolution_notes ? (
        <div className="mt-3 rounded-xl bg-[#f5f5f7] p-3">
          <p className="text-xs font-medium uppercase tracking-wide text-mist">Recorded ServiceNow resolution</p>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6">{payload.resolution_notes}</p>
        </div>
      ) : null}
      {guidance.length ? (
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm leading-6">
          {guidance.map((item) => <li key={item}>{item}</li>)}
        </ol>
      ) : null}
      {checks.length ? <p className="mt-3 text-xs leading-5 text-mist">Before anyone closes it: {checks.join(" ")}</p> : null}
      {payload.expected_outcome ? <p className="mt-2 text-xs leading-5 text-mist">{payload.expected_outcome}</p> : null}
      <p className="mt-3 text-xs leading-5 text-mist">
        {payload.model?.used ? `${payload.model.provider === "bedrock" ? "Bedrock" : "The model"} (${payload.model.model}) reviewed this draft. ` : ""}
        This does not close the incident. A person decides whether to resolve it, and nothing is written to ServiceNow from this screen.
      </p>
      {payload.disposition === "needs_approval" ? <Link className="mt-3 inline-block text-sm font-medium" to="/approvals">Open the approval</Link> : null}
    </Card>
  );
}

function WorkNotePanel({ logs, incidentId }: { logs?: RecommendationPayload["logs"]; incidentId: string }) {
  const note = (logs?.activity ?? []).find((item) => item.kind === "work_notes") ?? logs?.activity?.[0];
  const syslog = logs?.lines ?? [];
  const steps = note?.text ? noteSteps(note.text) : [];
  if (!syslog.length && !steps.length) return null;
  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-medium">{syslog.length ? "Syslog used for this recommendation" : "Work note used for this recommendation"}</h2>
        <Link className="text-xs font-medium" to={`/logs?incident=${incidentId}`}>Open in Log Intelligence</Link>
      </div>
      <p className="mt-2 text-sm leading-6 text-mist">
        {syslog.length
          ? `ServiceNow syslog returned ${syslog.length} line${syslog.length === 1 ? "" : "s"} for ${(logs?.searched ?? []).join(", ") || "this incident"}.`
          : `No syslog line matched ${(logs?.searched ?? []).join(", ") || "this incident"}. These steps are the work note recorded on the incident.`}
      </p>
      <div className="mt-3 divide-y divide-line">
        {syslog.length
          ? syslog.map((line) => (
            <div key={`${line.timestamp}-${line.message}`} className="py-3">
              <p className="text-xs text-mist">{line.timestamp} · {line.level || "INFO"} · {line.signature || "syslog"}</p>
              <p className="mt-1 text-sm leading-6">{line.message}</p>
            </div>
          ))
          : steps.map((step) => (
            <div key={step} className="py-3">
              <p className="text-xs text-mist">{note?.at} · {levelFor(step)} · {(logs?.searched ?? [])[0] || "work note"}</p>
              <p className="mt-1 text-sm leading-6">{step}</p>
            </div>
          ))}
      </div>
    </Card>
  );
}

function noteSteps(text: string) {
  return text
    .split("\n")
    .map((line) => line.replace(/^\s*\d+[\).:-]\s*/, "").trim())
    .filter((line) => line && !line.toLowerCase().startsWith("attaching"));
}

function levelFor(step: string) {
  const lowered = step.toLowerCase();
  if (lowered.includes("failed") || lowered.includes("failure")) return "ERROR";
  if (lowered.includes("not running")) return "WARN";
  return "INFO";
}

function LogBrief({ logs, incidentId }: { logs?: RecommendationPayload["logs"]; incidentId: string }) {
  if (!logs?.message && !logs?.lines?.length && !logs?.activity?.length) return null;
  const lines = logs.lines ?? [];
  return (
    <div className="mt-3 rounded-xl border border-line bg-[#f5f5f7] p-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-medium uppercase tracking-wide text-mist">Logs used for this recommendation</p>
        <Link className="text-xs font-medium" to={`/logs?incident=${incidentId}`}>Open in Log Intelligence</Link>
      </div>
      <p className="mt-2 text-sm leading-6">{logs.message}</p>
      {logs.searched?.length ? <p className="mt-2 text-xs text-mist">Searched ServiceNow syslog for {logs.searched.join(", ")}.</p> : null}
      {lines.length ? (
        <div className="mt-3 space-y-2">
          {lines.map((line) => (
            <p key={`${line.timestamp}-${line.message}`} className="rounded-lg bg-white px-3 py-2 font-mono text-xs leading-5">
              <span className="text-mist">{line.timestamp} {line.level}</span>
              <span className="mt-1 block">{line.message}</span>
            </p>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-xs leading-5 text-mist">No syslog line matched. The steps below are copied from the work note on this incident. Check the services that note names.</p>
      )}
    </div>
  );
}
