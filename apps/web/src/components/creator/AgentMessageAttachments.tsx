import { FileText } from "lucide-react";

interface AgentAttachmentSummary {
  name: string;
  char_count: number;
  chunk_count: number;
}

function getAgentAttachmentSummaries(parameters: Record<string, unknown>): AgentAttachmentSummary[] {
  const raw = parameters.uploaded_attachments;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const value = item as Record<string, unknown>;
    const name = typeof value.name === "string" ? value.name.trim() : "";
    if (!name) return [];
    return [{
      name,
      char_count: Number.isFinite(Number(value.char_count)) ? Number(value.char_count) : 0,
      chunk_count: Number.isFinite(Number(value.chunk_count)) ? Number(value.chunk_count) : 0,
    }];
  });
}

export function AgentMessageAttachments({ parameters }: { parameters: Record<string, unknown> }) {
  const attachments = getAgentAttachmentSummaries(parameters);
  if (!attachments.length) return null;
  return (
    <div className="agent-message-attachments" aria-label="本条消息的附件">
      {attachments.map((attachment, index) => (
        <span key={`${attachment.name}-${index}`} title={`${attachment.chunk_count} 个片段`}>
          <FileText size={12} />
          <b>{attachment.name}</b>
          <small>{attachment.char_count.toLocaleString()} 字</small>
        </span>
      ))}
    </div>
  );
}
