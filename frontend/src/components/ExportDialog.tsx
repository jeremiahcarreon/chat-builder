import { Download } from "lucide-react";
import { useState } from "react";
import { Button, Toggle } from "./ui";

/** Downloads the app as a .zip that any Chat Builder install can import. */
export function ExportButton({ appId }: { appId: string }) {
  const [open, setOpen] = useState(false);
  const [secrets, setSecrets] = useState(false);
  return (
    <div className="relative">
      <Button variant="secondary" onClick={() => setOpen((v) => !v)}>
        <Download size={14} /> Export
      </Button>
      {open && (
        <div className="absolute right-0 z-20 mt-2 w-96 space-y-3 rounded-lg border border-slate-200 bg-white p-4 text-sm shadow-lg">
          <p className="text-slate-600">
            Saves this app's settings, product knowledge, data sources (uploaded files and database connections with their table
            notes) and test set. Conversations and the workspace's API keys aren't included.
          </p>
          <Toggle
            checked={secrets}
            onChange={setSecrets}
            label="Include database passwords and the widget's signing secret"
            hint="For moving to a new server without re-entering them. Keep the file somewhere safe."
          />
          <div className="flex gap-2">
            <a href={`/api/apps/${appId}/export${secrets ? "?include_secrets=true" : ""}`} onClick={() => setOpen(false)}>
              <Button>Download</Button>
            </a>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
