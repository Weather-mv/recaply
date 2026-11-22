import { Clapperboard, Moon, Sun } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useBoot, useHealth, useProjects } from "@/hooks/data";
import { cn } from "@/lib/utils";
import { GlobalStatus } from "./GlobalStatus";

function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("rc_theme", dark ? "dark" : "light");
    } catch {
      /* private mode */
    }
  }, [dark]);
  return (
    <button
      type="button"
      onClick={() => setDark((d) => !d)}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      className="inline-flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
    >
      {dark ? <Sun className="size-5" /> : <Moon className="size-5" />}
    </button>
  );
}

const NAV = [
  { to: "/", label: "Projects", end: true },
  { to: "/settings", label: "Settings" },
];
const EXTERNAL = [
  { href: "/live", label: "Live" },
  { href: "/queue", label: "Queue" },
  { href: "/usage", label: "Usage" },
];

export function AppShell({ children, wide }: { children: ReactNode; wide?: boolean }) {
  const { session } = useBoot();
  const projects = useProjects();
  const health = useHealth();
  const initial = (session?.user.name || "?").slice(0, 1).toUpperCase();

  return (
    <TooltipProvider>
      <div className="min-h-screen">
        <header className="glass sticky top-0 z-40 border-b">
          <div className={cn("mx-auto flex h-16 items-center gap-6 px-4 sm:px-6", wide ? "max-w-[1800px]" : "max-w-7xl")}>
            <Link to="/" className="flex items-center gap-2.5 text-lg font-extrabold tracking-tight">
              <span className="grid size-9 place-items-center rounded-xl bg-primary text-primary-foreground">
                <Clapperboard className="size-5" />
              </span>
              Clipforge
              <span className="hidden rounded-full bg-secondary px-2 py-0.5 text-xs font-semibold text-muted-foreground sm:inline">Studio</span>
            </Link>
            <nav className="hidden items-center gap-1 md:flex">
              {NAV.map((n) => (
                <NavLink
                  key={n.to}
                  to={n.to}
                  end={n.end}
                  className={({ isActive }) =>
                    cn("rounded-full px-4 py-2 text-sm font-semibold transition-colors", isActive ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground")
                  }
                >
                  {n.label}
                </NavLink>
              ))}
              {EXTERNAL.map((n) => (
                <a key={n.href} href={n.href} className="rounded-full px-4 py-2 text-sm font-semibold text-muted-foreground transition-colors hover:text-foreground">
                  {n.label}
                </a>
              ))}
            </nav>
            <div className="ml-auto flex items-center gap-3">
              <GlobalStatus projects={projects.data ?? []} health={health.data} />
              <ThemeToggle />
              <a
                href="/account"
                title={session?.user.name ? `${session.user.name} · ${session.plan.name ?? ""}` : "Account"}
                className="grid size-10 place-items-center rounded-full bg-secondary text-sm font-bold"
              >
                {initial}
              </a>
            </div>
          </div>
        </header>
        <main className={cn("mx-auto px-4 py-6 sm:px-6", wide ? "max-w-[1800px]" : "max-w-7xl")}>{children}</main>
      </div>
    </TooltipProvider>
  );
}
