import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { api } from "../api";

interface Lesson {
  id: number;
  text: string;
  learned_at: string | null;
  origin: string | null;
  message_id: number | null;
}

/** What the assistant has learned from replies staff sent, with a way to forget a wrong one. */
export function LessonsPanel({ sourceId }: { sourceId: string }) {
  const queryClient = useQueryClient();
  const { data: lessons } = useQuery({
    queryKey: ["lessons", sourceId],
    queryFn: () => api<Lesson[]>(`/api/sources/${sourceId}/lessons`),
  });
  const forget = useMutation({
    mutationFn: (id: number) => api(`/api/sources/${sourceId}/lessons/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["lessons", sourceId] });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });

  if (!lessons) return <p className="mt-2 text-xs text-slate-500">Loading...</p>;
  if (lessons.length === 0) return <p className="mt-2 text-xs text-slate-500">Nothing learned yet.</p>;
  return (
    <div className="mt-2 rounded-md border border-slate-200 bg-slate-50">
      <p className="border-b border-slate-200 px-3 py-2 text-xs text-slate-500">
        Each lesson comes from a reply staff sent, with names, orders and other identifying details removed. Similar future
        questions see the matching lessons. Delete any that shouldn't be a rule.
      </p>
      <ul className="max-h-96 divide-y divide-slate-200 overflow-y-auto">
        {lessons.map((lesson) => (
          <li key={lesson.id} className="group flex items-start gap-2 px-3 py-2 text-sm">
            <div className="min-w-0 flex-1">
              {lesson.text.split("\n").map((line, i) => {
                const [label, ...rest] = line.split(": ");
                return rest.length ? (
                  <p key={i} className="text-slate-700">
                    <span className="font-medium text-slate-900">{label}:</span> {rest.join(": ")}
                  </p>
                ) : (
                  <p key={i}>{line}</p>
                );
              })}
              <p className="mt-1 text-xs text-slate-400">
                {lesson.learned_at ? new Date(lesson.learned_at).toLocaleString() : ""}
                {lesson.origin === "outcome" ? " · from an edited draft" : lesson.origin === "api" ? " · sent by your system" : ""}
              </p>
            </div>
            <button
              onClick={() => forget.mutate(lesson.id)}
              className="invisible rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600 group-hover:visible"
              title="Forget this lesson"
              aria-label="Forget this lesson"
            >
              <Trash2 size={14} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
