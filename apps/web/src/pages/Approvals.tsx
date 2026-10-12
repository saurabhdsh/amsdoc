import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, Field, Notice, Page, Pill, QueryState, inputClass } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { can, useAuth } from "../lib/auth";
import { titleCase } from "../lib/format";

type Approval = {
  id: string;
  status: string;
  summary: string;
  risk: string;
  comment: string;
  payload: {
    summary?: string;
    environment?: string;
    expected_outcome?: string;
    prerequisites?: string[];
    verification?: string[];
    rollback?: string;
    steps?: Array<{ action: string; params?: Record<string, string | string[]> }>;
  };
  incident: { number: string; short_description: string; source_label: string } | null;
  hypothesis: { statement: string; status: string; supporting?: string[] } | null;
  evidence: Array<{ id: string; title: string; excerpt: string; kind: string }>;
  progress: { state?: string };
};

export function Approvals() {
  const user = useAuth((state) => state.user);
  const client = useQueryClient();
  const [comment, setComment] = useState("");
  const [history, setHistory] = useState("");
  const [date, setDate] = useState("");
  const [participant, setParticipant] = useState("");
  const list = useQuery({ queryKey: ["approvals"], queryFn: () => api<{ items: Approval[] }>("/approvals") });
  const [selected, setSelected] = useState<string | null>(null);
  const current = list.data?.items.find((item) => item.id === selected) ?? list.data?.items[0];
  const decide = useMutation({
    mutationFn: (decision: string) => {
      const modifications: Record<string, string> = {};
      if (history.trim()) modifications.document_history = history.trim();
      if (date.trim()) modifications.planned_effective_date = date.trim();
      if (participant.trim()) modifications.participant_id = participant.trim();
      return api(`/approvals/${current?.id}/decide`, {
        method: "POST",
        idempotent: true,
        body: JSON.stringify({ decision, comment, modifications: Object.keys(modifications).length ? modifications : null }),
      });
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["approvals"] }),
  });
  const execute = useMutation({
    mutationFn: () => api(`/actions/${current?.id}/execute`, { method: "POST", idempotent: true }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["approvals"] });
      void client.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  return (
    <Page title="Resolution Approval Center" lede="Approval is bound to the exact action payload. Changing the values after approval invalidates it.">
      <QueryState loading={list.isLoading} error={list.error}>
        {!list.data?.items.length ? <Notice>No recommendations are waiting. Run an investigation from an Easidoc incident first.</Notice> : null}
        <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
          <Card className="space-y-2">
            {(list.data?.items ?? []).map((item) => (
              <button key={item.id} className="block w-full rounded-xl border border-line px-3 py-2 text-left text-sm" onClick={() => setSelected(item.id)}>
                <span className="font-medium">{item.incident?.number}</span>
                <span className="mt-1 block text-xs text-mist">{titleCase(item.status)}</span>
              </button>
            ))}
          </Card>
          {current ? (
            <Card className="space-y-4 text-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs uppercase tracking-wide text-mist">{current.incident?.source_label}</p>
                  <h2 className="serif text-3xl">{current.incident?.number}</h2>
                  <p className="mt-1">{current.incident?.short_description}</p>
                </div>
                <Pill tone={current.status === "approved" ? "teal" : "amber"}>{current.status}</Pill>
              </div>
              <p>{current.summary || current.payload.summary}</p>
              {current.hypothesis ? <p>Hypothesis ({current.hypothesis.status}): {current.hypothesis.statement}</p> : null}
              <div className="grid gap-3 md:grid-cols-2">
                <Info title="Expected outcome" body={current.payload.expected_outcome} />
                <Info title="Risk" body={current.risk} />
                <Info title="Prerequisites" body={(current.payload.prerequisites || []).join(" ")} />
                <Info title="Verification" body={(current.payload.verification || []).join(" ")} />
                <Info title="Rollback" body={current.payload.rollback} />
                <Info title="Environment" body={current.payload.environment} />
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-mist">Proposed steps</p>
                {(current.payload.steps || []).map((step, index) => <p key={index} className="mt-1">{step.action}</p>)}
              </div>
              {current.evidence.slice(0, 4).map((item) => <p key={item.id} className="text-mist">{item.kind}: {item.title} — {item.excerpt}</p>)}
              {current.status === "pending" && can(user, "approval.decide") ? (
                <div className="grid gap-3 md:grid-cols-2">
                  <Field label="Comment"><input className={inputClass} value={comment} onChange={(event) => setComment(event.target.value)} /></Field>
                  <Field label="Participant id, if changing assignment"><input className={inputClass} value={participant} onChange={(event) => setParticipant(event.target.value)} /></Field>
                  <Field label="Confirmed document history"><textarea className={inputClass} value={history} onChange={(event) => setHistory(event.target.value)} /></Field>
                  <Field label="Confirmed planned effective date"><input className={inputClass} placeholder="YYYY-MM-DD" value={date} onChange={(event) => setDate(event.target.value)} /></Field>
                  <div className="flex flex-wrap gap-2 md:col-span-2">
                    {["approve", "reject", "modify", "investigate", "escalate"].map((decision) => (
                      <Button key={decision} kind={decision === "approve" ? "teal" : decision === "reject" ? "danger" : "quiet"} onClick={() => decide.mutate(decision)}>{titleCase(decision)}</Button>
                    ))}
                  </div>
                </div>
              ) : null}
              {current.status === "approved" && can(user, "action.execute") ? <Button onClick={() => execute.mutate()}>Execute approved action</Button> : null}
              {current.progress?.state ? <p className="text-xs text-mist">Execution progress: {current.progress.state}</p> : null}
              {decide.error || execute.error ? <Notice tone="error">{errorMessage(decide.error || execute.error)}</Notice> : null}
              {execute.data ? <pre className="overflow-auto rounded-xl bg-paper p-3 text-xs">{JSON.stringify(execute.data, null, 2)}</pre> : null}
            </Card>
          ) : null}
        </div>
      </QueryState>
    </Page>
  );
}

function Info({ title, body }: { title: string; body?: string }) {
  return (
    <div className="rounded-xl bg-paper p-3">
      <p className="text-xs uppercase tracking-wide text-mist">{title}</p>
      <p className="mt-1">{body || "—"}</p>
    </div>
  );
}
