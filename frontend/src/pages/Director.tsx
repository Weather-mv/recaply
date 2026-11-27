import { useParams } from "react-router-dom";
import { DirectorWorkspace } from "@/components/director/Workspace";
import { AppShell } from "@/components/layout/AppShell";

export default function Director() {
  const { id, cid } = useParams();
  return (
    <DirectorWorkspace
      id={id}
      clipId={Number(cid)}
      frame={(content, { job, clip }) => (
        <AppShell
          wide
          crumbs={[
            { label: "Projects", to: "/projects" },
            ...(job ? [{ label: job.title || "Project", to: `/p/${job.id}${clip ? `?clip=${clip.id}` : ""}` }] : []),
            { label: "Director" },
          ]}
        >
          {content}
        </AppShell>
      )}
    />
  );
}
