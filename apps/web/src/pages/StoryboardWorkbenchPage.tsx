/** 旧“分镜工作台”入口兼容：第一阶段起统一进入分集视频。 */
import { Link, Navigate, useParams } from "react-router-dom";

export function StoryboardWorkbenchPage() {
  const projectId = Number(useParams().projectId);
  if (!Number.isSafeInteger(projectId) || projectId < 1) {
    return <main className="flow-page"><p role="alert">项目地址无效。<Link to="/projects">返回项目列表</Link></p></main>;
  }
  return <Navigate to={`/projects/${projectId}/episode-videos`} replace />;
}
