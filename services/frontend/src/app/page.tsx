import { Workspace } from "@/components/workspace";
import { loadContext } from "@/lib/backend";

// El estado se consulta en cada visita, nunca al compilar.
export const dynamic = "force-dynamic";

export default async function Home() {
  return <Workspace context={await loadContext()} />;
}
