import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Alert, Button, Card, Field, Input, Spinner } from "@/components/ui";
import { api, errorMessage, setUnauthorizedHandler } from "@/lib/api";

interface AuthStatus {
  setup_required: boolean;
  authenticated: boolean;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["auth"], queryFn: () => api.get<AuthStatus>("/api/auth/status"), staleTime: 60_000 });

  useEffect(() => {
    setUnauthorizedHandler(() => qc.invalidateQueries({ queryKey: ["auth"] }));
  }, [qc]);

  if (status.isLoading) return <Spinner className="h-screen" />;
  if (status.isError) {
    return (
      <div className="mx-auto max-w-md p-6">
        <Alert tone="danger" title="Cannot reach the backend">
          {errorMessage(status.error)}. Is the API running on port 8000?
        </Alert>
      </div>
    );
  }
  if (!status.data?.authenticated) {
    return <LoginScreen setup={!!status.data?.setup_required} onDone={() => qc.invalidateQueries()} />;
  }
  return <>{children}</>;
}

function LoginScreen({ setup, onDone }: { setup: boolean; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (setup && password.length < 10) return setError("Use at least 10 characters.");
    if (setup && password !== confirm) return setError("Passwords don't match.");
    setBusy(true);
    try {
      await api.post(setup ? "/api/auth/setup" : "/api/auth/login", { password });
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-5 flex items-center gap-2.5">
          <div className="rounded-lg bg-ink p-2 text-white">
            <Mail className="h-4 w-4" />
          </div>
          <div>
            <h1 className="text-base font-semibold">Outreach</h1>
            <p className="text-xs text-muted">{setup ? "First-run setup" : "Sign in to continue"}</p>
          </div>
        </div>
        <form onSubmit={submit} className="space-y-4">
          {setup && (
            <p className="text-sm text-muted">Create the admin password for this installation. It protects your leads, accounts and sending controls.</p>
          )}
          <Field label="Password" htmlFor="pw">
            <Input id="pw" type="password" autoComplete={setup ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
          </Field>
          {setup && (
            <Field label="Confirm password" htmlFor="pw2">
              <Input id="pw2" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </Field>
          )}
          {error && <Alert tone="danger">{error}</Alert>}
          <Button type="submit" variant="primary" className="w-full justify-center" loading={busy}>
            {setup ? "Create password" : "Sign in"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
