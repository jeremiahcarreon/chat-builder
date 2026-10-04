import { useQuery } from "@tanstack/react-query";
import { api, type DataSource } from "../api";
import { isBusy, SourceList } from "../components/SourceList";
import { Card } from "../components/ui";

export function DataSources() {
  const { data: sources, isLoading } = useQuery({
    queryKey: ["sources", "all"],
    queryFn: () => api<DataSource[]>("/api/sources"),
    refetchInterval: (query) => (isBusy(query.state.data) ? 2000 : false),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Data Sources</h1>
        <p className="mt-1 text-sm text-slate-500">
          Every file and database across your chat apps. Add new ones from an app's Data sources tab.
        </p>
      </div>
      <Card title="All data sources">
        {isLoading ? <p className="text-sm text-slate-500">Loading...</p> : <SourceList sources={sources ?? []} showApp />}
      </Card>
    </div>
  );
}
