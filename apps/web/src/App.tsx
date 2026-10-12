import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { api } from "./lib/api";
import { useAuth, type User } from "./lib/auth";
import { Admin } from "./pages/Admin";
import { Agents } from "./pages/Agents";
import { Analytics } from "./pages/Analytics";
import { Approvals } from "./pages/Approvals";
import { Assistant } from "./pages/Assistant";
import { Command } from "./pages/Command";
import { Connectors } from "./pages/Connectors";
import { Easidoc } from "./pages/Easidoc";
import { Incidents } from "./pages/Incidents";
import { Investigation } from "./pages/Investigation";
import { Knowledge } from "./pages/Knowledge";
import { Login } from "./pages/Login";
import { Logs } from "./pages/Logs";

function Guard() {
  const token = useAuth((state) => state.token);
  const user = useAuth((state) => state.user);
  const setSession = useAuth((state) => state.setSession);
  const clear = useAuth((state) => state.clear);
  const me = useQuery({
    queryKey: ["me", token],
    enabled: Boolean(token),
    queryFn: () => api<User>("/auth/me"),
  });

  useEffect(() => {
    if (token && me.data) setSession(token, me.data);
    if (me.isError) clear();
  }, [token, me.data, me.isError, setSession, clear]);

  if (!token || me.isError) return <Navigate to="/login" replace />;
  if (!user) return <div className="grid h-screen place-items-center text-sm text-mist">Opening the workspace…</div>;
  return <Shell />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Guard />}>
        <Route path="/" element={<Command />} />
        <Route path="/incidents" element={<Incidents />} />
        <Route path="/investigation" element={<Investigation />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/assistant" element={<Assistant />} />
        <Route path="/approvals" element={<Approvals />} />
        <Route path="/easidoc" element={<Easidoc />} />
        <Route path="/connectors" element={<Connectors />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/admin" element={<Admin />} />
      </Route>
    </Routes>
  );
}
