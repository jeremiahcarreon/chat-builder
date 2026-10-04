export function Placeholder({ title, step }: { title: string; step: number }) {
  return (
    <div>
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="mt-2 text-slate-500">Coming in build step {step}.</p>
    </div>
  );
}
