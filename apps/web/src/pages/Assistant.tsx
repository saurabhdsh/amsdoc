import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button, Card, Notice, Page, Pill, QueryState } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { titleCase } from "../lib/format";

type Message = { id: string; role: string; content: string; citations: Array<{ title?: string; source?: string }>; context: Record<string, unknown> };
type Conversation = { id: string; title: string; document_id: string | null; messages?: Message[]; context?: Record<string, unknown> };
type SimulatorDocument = { id: string; number: string; title: string; status: string };

export function Assistant() {
  const client = useQueryClient();
  const seeded = useRef(false);
  const [active, setActive] = useState<string | null>(null);
  const [draft, setDraft] = useState("I cannot start the approval workflow in Easidoc.");
  const [documentId, setDocumentId] = useState("");
  const [sending, setSending] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [notice, setNotice] = useState("");
  const [ready, setReady] = useState("");
  const [pending, setPending] = useState("");
  const [thought, setThought] = useState(0);
  const thread = useRef<HTMLDivElement>(null);
  const documents = useQuery({ queryKey: ["documents"], queryFn: () => api<{ items: SimulatorDocument[] }>("/simulator/documents") });
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: () => api<{ items: Conversation[] }>("/conversations") });
  const conversation = useQuery({
    queryKey: ["conversation", active],
    enabled: Boolean(active),
    queryFn: () => api<Conversation>(`/conversations/${active}`),
  });

  useEffect(() => {
    if (!documents.isSuccess || documents.data.items.length > 0 || seeded.current) return;
    seeded.current = true;
    setPreparing(true);
    setNotice("");
    void (async () => {
      try {
        await api("/simulator/scenarios/missing-periodic-review-participant/seed", { method: "POST", idempotent: true, body: JSON.stringify({ variant: null }) });
        await api("/simulator/scenarios/missing-document-metadata/seed", { method: "POST", idempotent: true, body: JSON.stringify({ variant: "A" }) });
        await client.invalidateQueries({ queryKey: ["documents"] });
      } catch (error) {
        seeded.current = false;
        setNotice(errorMessage(error));
      } finally {
        setPreparing(false);
      }
    })();
  }, [documents.isSuccess, documents.data?.items.length, client]);

  useEffect(() => {
    const attached = conversation.data?.document_id;
    if (attached) setDocumentId(attached);
  }, [conversation.data?.id, conversation.data?.document_id]);

  useEffect(() => {
    if (!sending) return;
    setThought(0);
    const timer = window.setInterval(() => setThought((value) => (value + 1) % 3), 1400);
    return () => window.clearInterval(timer);
  }, [sending]);

  useEffect(() => {
    if (!sending) return;
    thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: "smooth" });
  }, [sending, pending, thought]);

  const messages = conversation.data?.messages ?? [];
  const context = (messages.filter((item) => item.role === "assistant").at(-1)?.context ?? conversation.data?.context ?? {}) as {
    application?: { label?: string; name?: string };
    document?: {
      number?: string;
      title?: string;
      missing_fields?: string[];
      draft?: { document_history?: string; planned_effective_date?: string };
      review?: { tasks?: Array<{ assigned_participant_name?: string | null; status?: string }> };
    };
    incident?: { number?: string; state?: string; short_description?: string };
    knowledge?: Array<{ title?: string; excerpt?: string }>;
    logs?: Array<{ message?: string; signature?: string }>;
    diagnosis?: { case?: string; summary?: string };
    approval?: { id?: string; status?: string };
  };
  const chosen = (documents.data?.items ?? []).find((item) => item.id === documentId);
  const documentLabel = context.document
    ? `${context.document.number} · ${context.document.title}`
    : chosen
      ? `${chosen.number} · ${chosen.title}`
      : "No document is attached.";

  async function resetDemo() {
    setPreparing(true);
    setNotice("");
    setReady("");
    try {
      await api("/simulator/scenarios/missing-periodic-review-participant/reset", { method: "POST", idempotent: true, body: JSON.stringify({ variant: null }) });
      await api("/simulator/scenarios/missing-document-metadata/reset", { method: "POST", idempotent: true, body: JSON.stringify({ variant: "A" }) });
      setActive(null);
      setDocumentId("");
      setDraft("I cannot start the approval workflow in Easidoc.");
      setReady("Demo reset. DOC-1042 is missing a review participant. DOC-2088 is missing Document History.");
      await client.invalidateQueries({ queryKey: ["documents"] });
      await client.invalidateQueries({ queryKey: ["conversations"] });
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setPreparing(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim() || sending) return;
    const content = draft.trim();
    const started = Date.now();
    setPending(content);
    setDraft("");
    setSending(true);
    setNotice("");
    setReady("");
    try {
      let id = active;
      if (!id) {
        const row = await api<Conversation>("/conversations", { method: "POST", body: JSON.stringify({ document_id: documentId || null }) });
        id = row.id;
        setActive(id);
      } else if (documentId && conversation.data?.document_id !== documentId) {
        await api(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ document_id: documentId }) });
      }
      await api(`/conversations/${id}/messages`, { method: "POST", body: JSON.stringify({ content }) });
      const remain = 900 - (Date.now() - started);
      if (remain > 0) await new Promise((resolve) => window.setTimeout(resolve, remain));
      await client.invalidateQueries({ queryKey: ["conversation", id] });
      await client.invalidateQueries({ queryKey: ["conversations"] });
    } catch (error) {
      setDraft(content);
      setNotice(errorMessage(error));
    } finally {
      setPending("");
      setSending(false);
    }
  }

  return (
    <Page title="Conversational AI" lede="Ask about an Easidoc document, or ask about a live problem. A live problem is answered from closed incidents, published knowledge, and the logs that were actually found.">
      <div className="grid min-h-[680px] gap-4 lg:grid-cols-[1.15fr_0.85fr]">
        <Card className="flex flex-col">
          <div className="flex flex-wrap items-center gap-2">
            <select className="min-w-[280px] rounded-xl border border-line px-3 py-2 text-sm" value={documentId} onChange={(event) => setDocumentId(event.target.value)}>
              <option value="">{preparing ? "Loading Easidoc scenarios…" : "Select an Easidoc document"}</option>
              {(documents.data?.items ?? []).map((item) => (
                <option key={item.id} value={item.id}>{item.number} — {item.title}</option>
              ))}
            </select>
            <select className="rounded-xl border border-line px-3 py-2 text-sm" value={active ?? ""} onChange={(event) => setActive(event.target.value || null)}>
              <option value="">Previous</option>
              {(conversations.data?.items ?? []).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
            <Button kind="quiet" disabled={preparing || sending} onClick={() => void resetDemo()}>{preparing ? "Resetting…" : "Reset demo"}</Button>
          </div>
          {!documentId ? <p className="mt-3 text-sm text-mist">Select an Easidoc document for an approval question, or send a problem with no document selected.</p> : null}
          {notice ? <Notice tone="error">{notice}</Notice> : null}
          {ready ? <Notice>{ready}</Notice> : null}
          <div ref={thread} className="mt-4 flex-1 space-y-3 overflow-auto">
            <QueryState loading={conversation.isLoading && Boolean(active) && !sending} error={conversation.error || conversations.error}>
              {messages.map((message) => {
                const card = message.role === "assistant" ? answerCard(message.context) : null;
                return (
                  <div key={message.id} className={`max-w-[90%] rounded-2xl px-4 py-3 text-sm leading-6 ${message.role === "user" ? "ml-auto bg-navy text-white" : "bg-paper"}`}>
                    {card ? <AnswerCard card={card} /> : <p className="whitespace-pre-wrap">{message.content}</p>}
                    {!card && message.citations?.length ? <p className="mt-2 text-xs opacity-80">{message.citations.map((item) => item.title).filter(Boolean).join(" · ")}</p> : null}
                  </div>
                );
              })}
              {pending ? (
                <div className="ml-auto max-w-[90%] rounded-2xl bg-navy px-4 py-3 text-sm leading-6 text-white">
                  <p className="whitespace-pre-wrap">{pending}</p>
                </div>
              ) : null}
              {sending ? <Thinking step={thought} /> : null}
            </QueryState>
          </div>
          <form className="mt-4 flex gap-2" onSubmit={submit}>
            <input
              className="flex-1 rounded-full border border-line px-4 py-2 text-sm"
              value={draft}
              placeholder={
                context.diagnosis?.case === "missing_metadata"
                  ? "Document history: …  Planned effective date: YYYY-MM-DD"
                  : context.diagnosis?.case === "missing_participant"
                    ? "Assign Priya Shah"
                    : "Ask about this document"
              }
              onChange={(event) => setDraft(event.target.value)}
            />
            <Button type="submit" disabled={!draft.trim() || sending || preparing}>Send</Button>
          </form>
        </Card>
        <div className="space-y-3">
          <Notice>{context.application?.label || "EASIDOC — SIMULATED APPLICATION"}</Notice>
          <Card>
            <h2 className="text-sm font-medium">Application and incident</h2>
            <p className="mt-2 text-sm">{documentLabel}</p>
            <p className="mt-1 text-sm text-mist">{context.incident ? `${context.incident.number} · ${titleCase(context.incident.state)}` : "No open incident."}</p>
            {context.document?.draft?.document_history ? <p className="mt-2 text-sm">Document history: {context.document.draft.document_history}</p> : null}
            {context.document?.draft?.planned_effective_date ? <p className="mt-1 text-sm">Planned effective date: {context.document.draft.planned_effective_date}</p> : null}
            {(context.document?.review?.tasks ?? []).filter((task) => task.assigned_participant_name).map((task) => (
              <p key={task.assigned_participant_name} className="mt-1 text-sm">Reviewer: {task.assigned_participant_name}</p>
            ))}
            {context.diagnosis ? <p className="mt-3 text-sm">{context.diagnosis.summary}</p> : null}
            {context.diagnosis?.case ? <div className="mt-2"><Pill tone="navy">{titleCase(context.diagnosis.case)}</Pill></div> : null}
          </Card>
          <Card>
            <h2 className="text-sm font-medium">Retrieved knowledge</h2>
            {(context.knowledge ?? []).map((item, index) => <p key={index} className="mt-2 text-sm">{item.title}<span className="block text-xs text-mist">{item.excerpt}</span></p>)}
            {!context.knowledge?.length ? <p className="mt-2 text-sm text-mist">Citations appear after a grounded reply.</p> : null}
          </Card>
          <Card>
            <h2 className="text-sm font-medium">Relevant logs</h2>
            {(context.logs ?? []).map((item, index) => <p key={index} className="mt-2 text-sm">{item.signature} · {item.message}</p>)}
            {!context.logs?.length ? <p className="mt-2 text-sm text-mist">Log lines for the selected document appear with the reply.</p> : null}
            {context.approval ? <p className="mt-3 text-sm">Approval {context.approval.status}</p> : null}
          </Card>
        </div>
      </div>
    </Page>
  );
}

const THOUGHTS = ["Checking prior solutions", "Reading published knowledge", "Checking recorded logs"];

type AnswerCardData = {
  tool?: string;
  lead?: string;
  sections?: Array<{ label: string; body: string }>;
  codes?: Array<{ code: string; detail: string }>;
  references?: Array<{ kind: string; number: string; title: string; similarity: number; url?: string }>;
  logs?: string;
  offer?: string;
};

function answerCard(context: Record<string, unknown> | undefined): AnswerCardData | null {
  const card = context?.answer_card;
  if (!card || typeof card !== "object") return null;
  return card as AnswerCardData;
}

function AnswerCard({ card }: { card: AnswerCardData }) {
  return (
    <div className="space-y-3">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-mist">Using tool · {card.tool || "check_prior_solutions"}</p>
      <p className="text-[15px] font-medium leading-6 text-navy">{card.lead}</p>
      {(card.sections ?? []).map((section) => (
        <div key={section.label}>
          <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-mist">{section.label}</p>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{section.body}</p>
        </div>
      ))}
      {card.codes?.length ? (
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-mist">Status codes from the article</p>
          <div className="mt-2 divide-y divide-line rounded-xl border border-line bg-white">
            {card.codes.map((code) => (
              <div key={code.code} className="grid grid-cols-[52px_1fr] gap-3 px-3 py-2">
                <p className="font-medium">{code.code}</p>
                <p className="text-mist">{code.detail}</p>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {card.logs ? <p className="text-sm text-mist">{card.logs}</p> : null}
      {card.offer ? <p className="text-sm font-medium leading-6">{card.offer}</p> : null}
      {card.references?.length ? (
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-mist">References</p>
          <div className="mt-2 space-y-2">
            {card.references.map((item) => (
              <p key={`${item.kind}-${item.number}`} className="text-sm leading-5">
                <span className="font-medium">{item.kind}</span> {item.number}
                <span className="text-mist"> · {item.similarity}% match</span>
                <span className="block text-mist">{item.title}</span>
                {item.url ? <a className="text-navy underline decoration-[#d2d2d7] underline-offset-2" href={item.url} target="_blank" rel="noreferrer">{item.url}</a> : null}
              </p>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Thinking({ step }: { step: number }) {
  return (
    <div className="max-w-[90%] rounded-2xl bg-paper px-4 py-3" aria-live="polite">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1.5" aria-hidden="true">
          {[0, 1, 2].map((index) => (
            <span key={index} className="ams-dot h-1.5 w-1.5 rounded-full bg-navy" style={{ animationDelay: `${index * 0.16}s` }} />
          ))}
        </span>
        <p className="text-sm">Thinking</p>
      </div>
      <p className="mt-1 text-xs text-mist">{THOUGHTS[step]}</p>
    </div>
  );
}
