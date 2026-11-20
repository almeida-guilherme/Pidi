import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Logo } from "../components";
import { useSite } from "../site";

/** Logging in and signing up are the same: Google's page, then back here with an account. */
export default function LoginPage() {
  const location = useLocation();
  const [params] = useSearchParams();
  const site = useSite();
  const error = params.get("error"); // set by the server when a log-in didn't go through
  const next = (location.state as { from?: string } | null)?.from ?? "/";

  return (
    <div className="auth-shell">
      <header className="auth-header"><Logo to="/" large /></header>
      <main className="auth-main">
        <div className="auth-card">
          <div className="intro">
            <h1>Log in to Pidi</h1>
            <p>
              {site && !site.google && site.local_login
                ? "This server uses a log-in link instead of Google."
                : "Use your Google account. The first time, it creates your Pidi account."}
            </p>
          </div>
          {error && <p className="error" role="alert">{error}</p>}
          {site?.local_login && (
            <p className="notice">
              Local log-in is on: open the link the server printed when it started (in its terminal, or{" "}
              <code>docker logs</code>).
            </p>
          )}
          {site && !site.google ? (
            !site.local_login && <p className="notice">Log in with Google isn't set up on this server yet.</p>
          ) : (
            <a className="btn btn-outline btn-lg btn-block btn-google" href={api.googleLoginUrl(next)}>
              <GoogleIcon />
              Continue with Google
            </a>
          )}
          <p className="subtle small">
            Your recordings are processed as the <Link to="/privacy">privacy notice</Link> describes, including by
            an AI provider.
          </p>
        </div>
      </main>
    </div>
  );
}

function GoogleIcon() {
  return (
    <svg aria-hidden="true" width="20" height="20" viewBox="0 0 48 48">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}
