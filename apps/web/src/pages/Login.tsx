import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Field, inputClass } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { useAuth, type User } from "../lib/auth";

export function Login() {
  const navigate = useNavigate();
  const setSession = useAuth((state) => state.setSession);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <div className="grid min-h-screen bg-[#f5f5f7] lg:grid-cols-[38%_62%]">
      <section className="relative hidden overflow-hidden bg-[#1d1d1f] lg:flex lg:flex-col lg:justify-between">
        <img
          src="/TCS-logo-white.svg"
          alt="TCS"
          className="w-auto object-contain"
          style={{ height: 140, maxWidth: 300, alignSelf: "flex-start", paddingLeft: 24 }}
        />
        <div className="px-12 pb-12">
          <div className="text-[15px] font-semibold uppercase tracking-[0.24em] text-white/40">AI for AMS</div>
          <h1 className="mt-2 text-5xl font-semibold leading-tight text-white" style={{ letterSpacing: "-0.025em" }}>
            Autonomous
            <br />
            Operations
          </h1>
          <p className="mt-4 text-[15px] text-white/50">Investigate. Approve. Recover.</p>
        </div>
      </section>
      <section className="flex items-center justify-center bg-white p-8">
        <form
          className="w-full max-w-[340px] space-y-5"
          onSubmit={async (event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            const username = String(data.get("username") ?? "").trim();
            const password = String(data.get("password") ?? "").trim();
            setBusy(true);
            setError("");
            try {
              const result = await api<{ token: string; user: User }>("/auth/login", {
                method: "POST",
                body: JSON.stringify({ email: username, password }),
              });
              setSession(result.token, result.user);
              navigate("/");
            } catch (caught) {
              setError(errorMessage(caught));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="mb-8 lg:hidden">
            <img src="/TCS-logo-white.svg" alt="TCS" className="h-16 w-auto rounded-xl bg-[#1d1d1f] object-contain p-2" />
          </div>
          <div className="mb-8">
            <h2 className="text-[28px] font-semibold text-[#1d1d1f]" style={{ letterSpacing: "-0.022em" }}>
              Sign in
            </h2>
            <p className="mt-1 text-[14px] text-[#86868b]">Use your AI for AMS username and password.</p>
          </div>
          <Field label="Username">
            <input className={inputClass} name="username" defaultValue="Yugal" autoComplete="username" />
          </Field>
          <Field label="Password">
            <input className={inputClass} name="password" type="password" autoComplete="current-password" />
          </Field>
          {error ? <p className="text-[13px] text-red-600">{error}</p> : null}
          <Button className="w-full py-3" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </section>
    </div>
  );
}
