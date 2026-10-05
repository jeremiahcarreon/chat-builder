import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Loader2 } from "lucide-react";
import { api, type UpdateStatus } from "../api";

function when(iso: string): string {
  const date = new Date(iso);
  const hours = (date.getTime() - Date.now()) / 3_600_000;
  if (hours <= 0) return "shortly";
  if (hours < 1) return `in ${Math.max(1, Math.round(hours * 60))} minutes`;
  if (hours < 24) return `in about ${Math.round(hours)} hour${Math.round(hours) === 1 ? "" : "s"}`;
  return `on ${date.toLocaleString()}`;
}

/** Shows when a newer release is available, and installs it on request. */
export function UpdateBanner() {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["updates"],
    queryFn: () => api<UpdateStatus>("/api/system/updates"),
    // Poll faster while an update is underway.
    refetchInterval: (query) =>
      query.state.data?.status === "updating" || query.state.data?.requested ? 5_000 : 5 * 60_000,
  });
  const apply = useMutation({
    mutationFn: () => api<UpdateStatus>("/api/system/updates/apply", { method: "POST" }),
    onSuccess: (next) => queryClient.setQueryData(["updates"], next),
  });

  if (!data?.managed || !data.available || !data.latest) return null;
  const installing = data.status === "updating" || data.requested || apply.isPending;

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-sky-200 bg-sky-50 px-8 py-2.5 text-sm text-sky-900">
      {installing ? (
        <>
          <Loader2 size={16} className="animate-spin" />
          <span>
            Installing version {data.latest}. The dashboard will be unavailable for a minute or two, then reload this
            page.
          </span>
        </>
      ) : (
        <>
          <Download size={16} />
          <span>
            Version <strong>{data.latest}</strong> is available (you have {data.current}).
            {data.auto_update_at && ` It will install automatically ${when(data.auto_update_at)}.`}
            {data.status === "failed" && data.message && ` Last attempt: ${data.message}`}
          </span>
          {data.release_url && (
            <a href={data.release_url} target="_blank" rel="noreferrer" className="underline">
              What's new
            </a>
          )}
          <button
            onClick={() => apply.mutate()}
            className="ml-auto rounded-md bg-sky-700 px-3 py-1 font-medium text-white hover:bg-sky-800"
          >
            Update now
          </button>
          {apply.error && <span className="text-red-700">{apply.error.message}</span>}
        </>
      )}
    </div>
  );
}
