import { useEffect, useState, type ComponentProps } from "react";
import { useQuery } from "@tanstack/react-query";
import { toErrorMessage } from "@/api/client";
import { getSegmentVideoOptions, type SegmentVideoOptionsInput } from "@/api/segmentVideoOptions";
import { EpisodeSegmentPreview } from "./EpisodeSegmentPreview";

type Props = ComponentProps<typeof EpisodeSegmentPreview> & { input?: SegmentVideoOptionsInput; configuration?: unknown };

export function CheckedSegmentPreview({ input, ...props }: Props) {
  return input ? <CheckedPreview input={input} {...props} /> : <EpisodeSegmentPreview {...props} />;
}

function CheckedPreview({ input, configuration, ...props }: Props & {
  input: SegmentVideoOptionsInput;
}) {
  const serialized = JSON.stringify(input);
  const [settled, setSettled] = useState(serialized);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(serialized), 250);
    return () => window.clearTimeout(timer);
  }, [serialized]);
  const query = useQuery({ queryKey: ["segment-video-options", props.projectId, props.episodeId, settled, configuration],
    queryFn: () => getSegmentVideoOptions(props.projectId!, props.episodeId!, JSON.parse(settled)), retry: false,
    staleTime: 0, refetchOnWindowFocus: true });
  const fresh = settled === serialized && !query.isFetching && !query.isError;
  const result = fresh && query.data?.segment_id === input.segment_id && query.data?.plan_id === input.plan_id
    && query.data?.production_revision === input.production_revision ? query.data : undefined;
  const loading = settled !== serialized || query.isFetching;
  const issue = query.error ? toErrorMessage(query.error) : loading ? "正在检查当前模型与素材…" : result?.blocking_reason ?? "";
  return <><EpisodeSegmentPreview {...props} durationOptions={result?.durations ?? []}
    optionsIssue={issue} optionsBlocked={!result?.current_supported} />
    {query.isError && <button disabled={query.isFetching} onClick={() => void query.refetch()}>重新检查生成设置</button>}
  </>;
}
