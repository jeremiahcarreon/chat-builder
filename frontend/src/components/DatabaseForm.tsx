/** Database connection form (build step 5). */
export function DatabaseForm({ appId }: { appId: string }) {
  return <span data-app={appId} className="hidden" />;
}
