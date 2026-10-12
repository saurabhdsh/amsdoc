import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BookOpen,
  Bot,
  Cable,
  CheckCheck,
  FileStack,
  LayoutDashboard,
  MessageSquareText,
  ScrollText,
  Search,
  Settings,
  ShieldCheck,
  Workflow,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { LogOut } from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { titleCase } from "../lib/format";

const NAV = [
  { to: "/", label: "Command Center", icon: LayoutDashboard },
  { to: "/incidents", label: "Incidents", icon: ScrollText },
  { to: "/investigation", label: "AI Investigation Studio", icon: Workflow },
  { to: "/agents", label: "Agent Control Center", icon: Bot },
  { to: "/logs", label: "Log Intelligence", icon: Activity },
  { to: "/knowledge", label: "Knowledge Fabric", icon: BookOpen },
  { to: "/assistant", label: "Conversational AI", icon: MessageSquareText },
  { to: "/approvals", label: "Resolution Approval Center", icon: CheckCheck },
  { to: "/easidoc", label: "Easidoc Digital Twin", icon: FileStack },
  { to: "/connectors", label: "Application Connectors", icon: Cable },
  { to: "/analytics", label: "Analytics and Outcomes", icon: ShieldCheck },
  { to: "/admin", label: "Administration and Settings", icon: Settings },
];

type LiveEvent = { id: number; name: string; summary?: string };

export function Shell() {
  const user = useAuth((state) => state.user);
  const token = useAuth((state) => state.token);
  const clear = useAuth((state) => state.clear);
  const navigate = useNavigate();
  const client = useQueryClient();
  const [palette, setPalette] = useState(false);
  const [query, setQuery] = useState("");
  const [live, setLive] = useState<LiveEvent[]>([]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette(true);
      }
      if (event.key === "Escape") setPalette(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!token) return;
    let stop = false;
    let socket: WebSocket | undefined;
    const connect = () => {
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/events/stream`);
      socket.onopen = () => socket?.send(JSON.stringify({ type: "auth", token, after_id: 0 }));
      let ready = false;
      let replayTimer = 0;
      socket.onmessage = (message) => {
        const data = JSON.parse(message.data);
        if (data.type !== "event") return;
        setLive((current) => [{ id: data.id, name: data.name, summary: data.summary }, ...current].slice(0, 6));
        window.clearTimeout(replayTimer);
        replayTimer = window.setTimeout(() => {
          ready = true;
        }, 400);
        if (!ready) return;
        ["kpis", "incidents", "approvals", "documents", "document", "investigation", "events", "logs", "runs", "conversation"].forEach((key) => {
          void client.invalidateQueries({ queryKey: [key] });
        });
      };
      socket.onclose = () => {
        if (!stop) window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      stop = true;
      socket?.close();
    };
  }, [token, client]);

  const search = useQuery({
    queryKey: ["search", query],
    enabled: palette && query.trim().length > 1,
    queryFn: () => api<{ incidents: Array<{ id: string; number: string; short_description: string }>; documents: Array<{ id: string; number: string; title: string }>; knowledge: Array<{ id: string; title: string }> }>(`/search?q=${encodeURIComponent(query)}`),
  });

  const health = useQuery({ queryKey: ["health"], queryFn: () => api<{ servicenow: string; simulator: { label: string } }>("/health") });
  const items = useMemo(() => NAV, []);

  const parts = (user?.name ?? "").split(" ").filter(Boolean);
  const initials = parts.length >= 2 ? parts[0][0] + parts[parts.length - 1][0] : (user?.name ?? "A").slice(0, 2);

  return (
    <div className="flex h-screen bg-white text-[#1d1d1f]">
      <aside className="flex w-[248px] shrink-0 flex-col border-r border-[#e8e8ed] bg-[#f5f5f7]">
        <div className="bg-[#1d1d1f] px-3 pb-3 pt-0">
          <img src="/TCS-logo-white.svg" alt="TCS" className="w-auto object-contain object-left" style={{ height: 72, maxWidth: 196 }} />
          <div className="px-2 pb-2">
            <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-white/40">AI for AMS</div>
            <div className="mt-0.5 text-[15px] font-semibold text-white" style={{ letterSpacing: "-0.022em" }}>Autonomous Operations</div>
          </div>
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 overflow-auto px-2 py-3">
          {items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] font-medium ${isActive ? "bg-white text-[#1d1d1f] shadow-[0_1px_3px_rgba(0,0,0,0.06)]" : "text-[#86868b] hover:bg-white/60 hover:text-[#1d1d1f]"}`
              }
            >
              <item.icon size={15} />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-[#e8e8ed] px-4 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#1d1d1f] text-[11px] font-semibold uppercase text-white">{initials}</div>
            <div className="min-w-0 flex-1 truncate text-[13px] font-semibold">{user?.name}</div>
            <button type="button" title="Sign out" className="rounded-md p-1.5 text-[#86868b] hover:bg-white hover:text-[#1d1d1f]" onClick={() => { clear(); navigate("/login"); }}>
              <LogOut size={14} />
            </button>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-[#e8e8ed] bg-white/80 px-8 py-3 backdrop-blur-xl">
          <button onClick={() => setPalette(true)} className="flex w-full max-w-md items-center gap-2 rounded-lg border border-[#e8e8ed] bg-[#f5f5f7] px-3 py-2 text-left text-[13px] text-[#86868b]">
            <Search size={15} />
            Search incidents, documents, knowledge
            <span className="ml-auto rounded-md border border-[#e8e8ed] bg-white px-1.5 text-[11px]">⌘K</span>
          </button>
          <div className="flex shrink-0 items-center gap-2 text-[11px] font-medium text-[#86868b]">
            <span className="rounded-full border border-[#e8e8ed] px-3 py-1">{health.data?.simulator.label ?? "EASIDOC — SIMULATED APPLICATION"}</span>
            <span className="rounded-full border border-[#e8e8ed] px-3 py-1">ServiceNow {health.data?.servicenow === "configured" ? "configured" : "not configured"}</span>
          </div>
        </header>
        {live.length > 0 ? (
          <div className="flex gap-2 overflow-auto border-b border-line bg-white px-6 py-2 text-xs text-mist">
            {live.map((event) => (
              <span key={event.id} className="rounded-full bg-paper px-3 py-1">{titleCase(event.name)}</span>
            ))}
          </div>
        ) : null}
        <main className="min-h-0 flex-1 overflow-auto px-6 py-6">
          <Outlet />
        </main>
      </div>
      {palette ? (
        <div className="fixed inset-0 z-30 grid place-items-start bg-ink/30 px-4 pt-24" onClick={() => setPalette(false)}>
          <div className="mx-auto w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-card" onClick={(event) => event.stopPropagation()}>
            <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search the account" className="w-full border-b border-line px-5 py-4 text-base outline-none" />
            <div className="max-h-96 overflow-auto p-3 text-sm">
              {(search.data?.incidents ?? []).map((item) => (
                <button key={item.id} className="block w-full rounded-xl px-3 py-2 text-left hover:bg-paper" onClick={() => { setPalette(false); navigate(`/investigation?incident=${item.id}`); }}>
                  <span className="font-medium">{item.number}</span> <span className="text-mist">{item.short_description}</span>
                </button>
              ))}
              {(search.data?.documents ?? []).map((item) => (
                <button key={item.id} className="block w-full rounded-xl px-3 py-2 text-left hover:bg-paper" onClick={() => { setPalette(false); navigate(`/easidoc?document=${item.id}`); }}>
                  <span className="font-medium">{item.number}</span> <span className="text-mist">{item.title}</span>
                </button>
              ))}
              {(search.data?.knowledge ?? []).map((item) => (
                <button key={item.id} className="block w-full rounded-xl px-3 py-2 text-left hover:bg-paper" onClick={() => { setPalette(false); navigate("/knowledge"); }}>
                  {item.title}
                </button>
              ))}
              {query.trim().length > 1 && !search.isFetching && !search.data?.incidents.length && !search.data?.documents.length && !search.data?.knowledge.length ? (
                <p className="px-3 py-6 text-mist">No matches in this account.</p>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
