import { useEffect, useState } from "react";
import { api, type Run, type Site } from "./api";

let cached: Promise<Site> | null = null;

/** Public facts about the site (who runs it, retention), fetched once per page load. */
export function useSite(): Site | null {
  const [site, setSite] = useState<Site | null>(null);
  useEffect(() => {
    cached ??= api.site();
    cached.then(setSite, () => (cached = null));
  }, []);
  return site;
}

/** "Pidi template · English", or the company template's name. */
export function templateLabel(run: Run, site: Site | null): string {
  if (run.template_name) return run.template_name;
  if (!run.language) return "Pidi template";
  return `Pidi template · ${site?.languages.find((l) => l.code === run.language)?.name ?? run.language}`;
}

export function retentionText(days: number): string {
  return days > 0 ? `${days} day${days === 1 ? "" : "s"} after its document is ready` : "when you delete the project";
}
