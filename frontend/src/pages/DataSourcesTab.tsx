import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { api, uploadFiles, type DataSource } from "../api";
import { DatabaseForm } from "../components/DatabaseForm";
import { isBusy, SourceList } from "../components/SourceList";
import { Card } from "../components/ui";

const ACCEPT = ".pdf,.docx,.txt,.md,.markdown,.html,.htm,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.xlsx,.xlsm,.csv,.db,.sqlite,.sqlite3";

export function DataSourcesTab({ appId }: { appId: string }) {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: sources } = useQuery({
    queryKey: ["sources", appId],
    queryFn: () => api<DataSource[]>(`/api/apps/${appId}/sources`),
    refetchInterval: (query) => (isBusy(query.state.data) ? 2000 : false),
  });

  async function upload(files: FileList | File[] | null) {
    if (!files || !files.length) return;
    setUploading(true);
    setError(null);
    try {
      await uploadFiles(appId, Array.from(files));
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["apps"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    upload(event.dataTransfer.files);
  }

  return (
    <div className="space-y-6">
      <Card
        title="Upload files"
        description="Documents (PDF, Word, text, Markdown, HTML, and scanned PDFs or images, which are read with OCR) are searched automatically. Spreadsheets (Excel, CSV) and SQLite files become tables the assistant can query."
      >
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-10 text-center transition ${
            dragging ? "border-slate-500 bg-slate-50" : "border-slate-300 hover:border-slate-400"
          }`}
        >
          <Upload className="text-slate-400" />
          <p className="mt-2 text-sm font-medium">{uploading ? "Uploading..." : "Drop files here or click to choose"}</p>
          <p className="mt-1 text-xs text-slate-500">Up to 50 MB per file</p>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => upload(e.target.files)}
          />
        </div>
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      </Card>

      <DatabaseForm appId={appId} />

      <Card title="This app's data sources">
        <SourceList sources={sources ?? []} />
      </Card>
    </div>
  );
}
