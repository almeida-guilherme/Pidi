import { createContext, useCallback, useContext, useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, type User } from "./api";
import { Logo, initials } from "./components";
import { useSite } from "./site";
import AccountPage from "./pages/AccountPage";
import CostsPage from "./pages/CostsPage";
import CreditsPage from "./pages/CreditsPage";
import LandingPage from "./pages/LandingPage";
import LoginPage from "./pages/LoginPage";
import NewProjectPage from "./pages/NewProjectPage";
import RoiPage from "./pages/RoiPage";
import RoiTemplatePage from "./pages/RoiTemplatePage";
import PrivacyPage from "./pages/PrivacyPage";
import ProjectsPage from "./pages/ProjectsPage";
import RunPage from "./pages/RunPage";
import ReviewPage from "./pages/ReviewPage";
import TemplatePage from "./pages/TemplatePage";
import TemplatesPage from "./pages/TemplatesPage";

type Auth = { user: User | null; setUser: (user: User | null) => void };
const AuthContext = createContext<Auth>({ user: null, setUser: () => {} });
export const useAuth = () => useContext(AuthContext);

/** The credit balance in the header; pages that change it (buying, uploading) call refresh. */
type CreditsState = { balance: number | null; refresh: () => void };
const CreditsContext = createContext<CreditsState>({ balance: null, refresh: () => {} });
export const useCredits = () => useContext(CreditsContext);

export default function App() {
  // undefined while the session is being checked, null when logged out.
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    api.me().then(setUser, () => setUser(null));
  }, []);

  if (user === undefined) return <div className="page-loading" aria-busy="true" />;

  const app = (page: ReactNode) => <RequireUser><Shell>{page}</Shell></RequireUser>;
  return (
    <AuthContext.Provider value={{ user, setUser }}>
      <Routes>
        {/* Logged out, "/" is the landing page; logged in, it is the list of projects. */}
        <Route path="/" element={user ? app(<ProjectsPage />) : <LandingPage />} />
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
        <Route path="/projects/new" element={app(<NewProjectPage />)} />
        <Route path="/runs/:id" element={app(<RunPage />)} />
        <Route path="/runs/:id/review" element={app(<ReviewPage />)} />
        <Route path="/templates" element={app(<TemplatesPage />)} />
        <Route path="/templates/:id" element={app(<TemplatePage />)} />
        <Route path="/account" element={app(<AccountPage />)} />
        <Route path="/dashboard" element={app(<CostsPage />)} />
        <Route path="/costs" element={<Navigate to="/dashboard" replace />} />
        <Route path="/roi" element={app(<RoiPage />)} />
        <Route path="/roi/templates/new" element={app(<RoiTemplatePage />)} />
        <Route path="/roi/templates/:id" element={app(<RoiTemplatePage />)} />
        <Route path="/credits" element={app(<CreditsOnly><CreditsPage /></CreditsOnly>)} />
        <Route path="/credits/:purchaseId" element={app(<CreditsOnly><CreditsPage /></CreditsOnly>)} />
        <Route path="/register" element={<Navigate to="/login" replace />} />
        <Route path="/privacy" element={<PrivacyPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthContext.Provider>
  );
}

/** Pages about credits, which exist only when the site uses them (billing "credits"); with own keys, the
Account page, where keys are connected. */
function CreditsOnly({ children }: { children: ReactNode }) {
  const site = useSite();
  if (!site) return null;
  return site.billing === "credits" ? children : <Navigate to="/account" replace />;
}

function RequireUser({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const location = useLocation();
  return user ? children : <Navigate to="/login" replace state={{ from: location.pathname }} />;
}

function Shell({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const site = useSite();
  const usesCredits = site?.billing === "credits";
  const [credits, setCredits] = useState<number | null>(null);
  const refresh = useCallback(() => {
    if (usesCredits) api.credits().then((c) => setCredits(c.balance), () => {});
  }, [usesCredits]);
  // Read again on every page change, so it follows uploads, deleted projects and credits given meanwhile.
  useEffect(() => { refresh(); }, [pathname, refresh]);
  const inProjects = pathname === "/" || pathname.startsWith("/projects") || pathname.startsWith("/runs");
  return (
    <CreditsContext.Provider value={{ balance: credits, refresh }}>
      <header className="app-header">
        <div className="app-header-inner">
          <Logo to="/" label="Pidi projects" />
          <nav aria-label="App" className="app-nav">
            <NavLink to="/" className={inProjects ? "active" : undefined} aria-current={inProjects ? "page" : undefined}>
              Projects
            </NavLink>
            <NavLink to="/templates">Templates</NavLink>
            <NavLink to="/roi">ROI</NavLink>
            {site?.billing === "keys" && <NavLink to="/dashboard">Dashboard</NavLink>}
            {usesCredits && credits !== null && (
              <>
                <NavLink to="/credits">Buy credits</NavLink>
                <span className={`credits-pill${credits > 0 ? "" : " empty"}`}
                      title="Your credits: a project costs one per started minute of video.">
                  {credits} credit{credits === 1 ? "" : "s"}
                </span>
              </>
            )}
            {user && <AccountMenu user={user} />}
          </nav>
        </div>
      </header>
      {user && <AccountNotice user={user} />}
      {children}
    </CreditsContext.Provider>
  );
}

/** New accounts (and everyone, after the notice changes) accept the privacy notice before creating projects. */
function AccountNotice({ user }: { user: User }) {
  const { setUser } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function accept() {
    setBusy(true);
    try {
      setUser(await api.acceptPrivacy());
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  if (user.privacy_accepted) return null;
  return (
    <div className="account-notice" role="region" aria-label="Your account needs attention">
      <div className="notice">
        <p>
          <strong>Please accept the privacy notice.</strong> It explains what happens to your recordings, including
          that they are processed by an AI provider. You can create projects after accepting it.
        </p>
        <div className="notice-actions">
          <Link to="/privacy" className="btn btn-outline">Read it</Link>
          <button type="button" className="btn btn-dark" disabled={busy} onClick={accept}>I accept</button>
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

export function useLogout() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  return async () => {
    await api.logout().catch(() => {});
    setUser(null);
    navigate("/");
  };
}

/**
 * The avatar; hovering it (or focusing it with the keyboard) opens a menu with the account and Log out.
 * Touch screens have no hover, so there a tap opens the menu instead of going to the account page.
 */
function AccountMenu({ user }: { user: User }) {
  const logout = useLogout();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { pathname } = useLocation();
  useEffect(() => {
    // Leaving the page closes the menu, also when it is open because a link in it has focus.
    setOpen(false);
    if (ref.current?.contains(document.activeElement)) (document.activeElement as HTMLElement).blur();
  }, [pathname]);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => ref.current?.contains(e.target as Node) || setOpen(false);
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  function tap(e: MouseEvent) {
    if (window.matchMedia("(hover: none)").matches) {
      e.preventDefault();
      setOpen(!open);
    }
  }

  return (
    <div className={open ? "account-menu open" : "account-menu"} ref={ref}>
      <NavLink to="/account" className="avatar" aria-label="Your account" aria-haspopup="menu" aria-expanded={open}
               onClick={tap}>
        {initials(user)}
      </NavLink>
      <div className="account-pop">
        <div className="account-panel" role="menu">
          <div className="account-who">
            {user.name && <strong>{user.name}</strong>}
            <span>{user.email}</span>
          </div>
          <NavLink to="/account" role="menuitem">Account</NavLink>
          <button type="button" role="menuitem" onClick={logout}>Log out</button>
        </div>
      </div>
    </div>
  );
}
