import { useQuery } from "@tanstack/react-query";
import { Card, Page, Pill, QueryState } from "../components/ui";
import { api } from "../lib/api";
import { titleCase, when } from "../lib/format";

type Agent = { id: string; name: string; summary: string; tools: string[] };
type Run = { id: string; status: string; orchestrator: string; incident_number: string | null; started_at: string; error: string | null; steps: Array<{ agent: string; status: string }> };

export function Agents() {
  const catalog = useQuery({ queryKey: ["agents"], queryFn: () => api<{ agents: Agent[]; orchestrator: string }>("/agents") });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api<{ items: Run[] }>("/agents/runs") });

  return (
    <Page title="Agent Control Center" lede="Eleven specialized agents share one orchestration path. Tool use stays on the server allowlist.">
      <QueryState loading={catalog.isLoading} error={catalog.error}>
        <p className="text-sm text-mist">Orchestrator in use: {catalog.data?.orchestrator}</p>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(catalog.data?.agents ?? []).map((agent) => (
            <Card key={agent.id}>
              <p className="text-xs uppercase tracking-wide text-teal">{agent.id}</p>
              <h2 className="mt-1 font-medium">{agent.name}</h2>
              <p className="mt-2 text-sm leading-6 text-mist">{agent.summary}</p>
              <p className="mt-3 text-xs text-mist">{agent.tools.join(" · ") || "No external tools"}</p>
            </Card>
          ))}
        </div>
      </QueryState>
      <Card>
        <h2 className="text-sm font-medium">Recent runs</h2>
        <QueryState loading={runs.isLoading} error={runs.error}>
          <div className="mt-3 divide-y divide-line text-sm">
            {(runs.data?.items ?? []).map((run) => (
              <div key={run.id} className="flex items-center justify-between gap-3 py-3">
                <div>
                  <p className="font-medium">{run.incident_number ?? "Incident"} · {run.orchestrator}</p>
                  <p className="text-xs text-mist">{when(run.started_at)} · {run.steps.map((step) => titleCase(step.agent)).join(" → ")}</p>
                  {run.error ? <p className="text-xs text-rose-700">{run.error}</p> : null}
                </div>
                <Pill tone={run.status === "completed" ? "teal" : "amber"}>{run.status}</Pill>
              </div>
            ))}
            {!runs.data?.items.length ? <p className="py-6 text-mist">No agent runs have been recorded.</p> : null}
          </div>
        </QueryState>
      </Card>
    </Page>
  );
}
