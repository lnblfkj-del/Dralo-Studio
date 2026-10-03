import { TextGenerationIcon } from "@/components/ui/TextGenerationLoading";
import { Ban, CheckCircle2, FileText, Image as ImageIcon, Layers3, LoaderCircle, Music2, Video, XCircle } from "lucide-react";
import type { JobStatus } from "@/types/api";
import { ACTIVE, STATUS_LABELS, typeGroup } from "./taskCenterModel";

export function TypeIcon({ jobType, size = 16 }: { jobType: string; size?: number }) {
  const group = typeGroup(jobType);
  if (group === "text") return <FileText size={size} />;
  if (group === "image") return <ImageIcon size={size} />;
  if (group === "video") return <Video size={size} />;
  if (group === "audio") return <Music2 size={size} />;
  return <Layers3 size={size} />;
}

export function JobStatusBadge({ status, jobType }: { status: JobStatus; jobType?: string }) {
  return (
    <span className={`task-status task-status--${status}`}>
      {ACTIVE.has(status) && (jobType && typeGroup(jobType) === "text" ? <TextGenerationIcon size={18} /> : <LoaderCircle className="task-spin" size={12} />)}
      {status === "succeeded" && <CheckCircle2 size={12} />}
      {status === "failed" && <XCircle size={12} />}
      {status === "cancelled" && <Ban size={12} />}
      {STATUS_LABELS[status]}
    </span>
  );
}
