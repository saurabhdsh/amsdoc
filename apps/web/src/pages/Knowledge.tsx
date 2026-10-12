import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, Field, Notice, Page, Pill, QueryState, inputClass } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { can, useAuth } from "../lib/auth";

type Article = { id: string; title: string; source: string; source_label: string; status: string; excerpt: string; trusted: boolean; application_code: string };

const NEXT: Record<string, string> = { draft: "reviewed", reviewed: "approved", approved: "published", published: "superseded" };

export function Knowledge() {
  const user = useAuth((state) => state.user);
  const client = useQueryClient();
  const [query, setQuery] = useState("periodic review participant");
  const [selected, setSelected] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["knowledge"], queryFn: () => api<{ items: Article[] }>("/knowledge") });
  const search = useQuery({
    queryKey: ["knowledge-search", query],
    enabled: query.trim().length > 2,
    queryFn: () => api<{ items: Article[] }>("/knowledge/search", { method: "POST", body: JSON.stringify({ query, application_code: "easidoc" }) }),
  });
  const detail = useQuery({
    queryKey: ["knowledge", selected],
    enabled: Boolean(selected),
    queryFn: () => api<Article & { content: string }>(`/knowledge/${selected}`),
  });
  const transition = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => api(`/knowledge/${id}/transition`, { method: "POST", body: JSON.stringify({ status }) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["knowledge"] }),
  });
  const upload = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      body.append("application_code", "easidoc");
      return api("/knowledge/upload", { method: "POST", body });
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["knowledge"] }),
  });

  return (
    <Page title="Knowledge Fabric" lede="Published articles are eligible for retrieval. AI drafts stay untrusted until a person publishes them.">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Hybrid search">
          <input className={`${inputClass} w-80`} value={query} onChange={(event) => setQuery(event.target.value)} />
        </Field>
        {can(user, "knowledge.write") ? (
          <label className="cursor-pointer rounded-full border border-line bg-white px-4 py-2 text-sm">
            Upload document
            <input type="file" className="hidden" accept=".txt,.md,.pdf,.docx" onChange={(event) => { const file = event.target.files?.[0]; if (file) upload.mutate(file); }} />
          </label>
        ) : null}
      </div>
      {upload.error ? <Notice tone="error">{errorMessage(upload.error)}</Notice> : null}
      <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
        <QueryState loading={list.isLoading} error={list.error}>
          <Card className="max-h-[640px] overflow-auto">
            <h2 className="text-sm font-medium">Catalog</h2>
            {(list.data?.items ?? []).map((item) => (
              <button key={item.id} className="mt-3 block w-full rounded-xl border border-line p-3 text-left" onClick={() => setSelected(item.id)}>
                <div className="flex items-center justify-between gap-2">
                  <p className="font-medium">{item.title}</p>
                  <Pill tone={item.trusted ? "teal" : "amber"}>{item.status}</Pill>
                </div>
                <p className="mt-1 text-xs text-mist">{item.source_label} · {item.application_code || "unscoped"}</p>
              </button>
            ))}
          </Card>
        </QueryState>
        <div className="space-y-4">
          <Card>
            <h2 className="text-sm font-medium">Search results</h2>
            {(search.data?.items ?? []).map((item) => (
              <button key={item.id} className="mt-3 block w-full text-left" onClick={() => setSelected(item.id)}>
                <p className="text-sm font-medium">{item.title}</p>
                <p className="text-xs text-mist">{item.excerpt}</p>
              </button>
            ))}
            {search.data && !search.data.items.length ? <p className="mt-3 text-sm text-mist">No published article matched.</p> : null}
          </Card>
          {detail.data ? (
            <Card>
              <div className="flex items-center justify-between">
                <h2 className="serif text-2xl">{detail.data.title}</h2>
                <Pill tone={detail.data.trusted ? "teal" : "amber"}>{detail.data.trusted ? "trusted" : "not trusted"}</Pill>
              </div>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-mist">{detail.data.content}</p>
              {NEXT[detail.data.status] && (can(user, "knowledge.review") || can(user, "knowledge.publish")) ? (
                <div className="mt-4"><Button onClick={() => transition.mutate({ id: detail.data!.id, status: NEXT[detail.data!.status] })}>Move to {NEXT[detail.data.status]}</Button></div>
              ) : null}
              {transition.error ? <p className="mt-2 text-sm text-rose-700">{errorMessage(transition.error)}</p> : null}
            </Card>
          ) : null}
        </div>
      </div>
    </Page>
  );
}
