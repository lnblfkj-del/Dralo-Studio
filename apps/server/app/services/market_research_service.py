"""真实 Web 搜索驱动的短剧市场探查服务。"""

import json
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from app.core.outbound_http import outbound_client
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.core.errors import ConflictError, NotFoundError, ProviderError
from app.core.provider_crypto import decrypt_api_key
from app.models import Job, MarketIdeaProject, MarketResearchRun, Provider, ProviderModel, utcnow
from app.providers.factory import create_provider_adapter
from app.schemas.market_research import MarketResearchReport
from app.services import job_service, provider_service
from app.services.structured_output_service import parse_structured_result
from app.services.team_access import owner_scope, same_team

TARGET_MARKET_RESEARCH = "market_research"
TAVILY_BASE_URL = "https://api.tavily.com"
RUN_ACTIVE = {"queued", "processing"}


async def adopted_projects_by_run(
    session: AsyncSession, run_ids: list[int]
) -> dict[int, dict[int, int]]:
    """Return project bindings at idea granularity for API rendering."""
    if not run_ids:
        return {}
    rows = (
        await session.execute(
            select(
                MarketIdeaProject.market_research_run_id,
                MarketIdeaProject.idea_index,
                MarketIdeaProject.project_id,
            ).where(MarketIdeaProject.market_research_run_id.in_(run_ids))
        )
    ).all()
    result: dict[int, dict[int, int]] = {run_id: {} for run_id in run_ids}
    for run_id, idea_index, project_id in rows:
        result.setdefault(run_id, {})[idea_index] = project_id
    return result


async def get_run(session: AsyncSession, run_id: int, owner_id: int) -> MarketResearchRun:
    item = await session.scalar(
        select(MarketResearchRun).where(
            MarketResearchRun.id == run_id,
            owner_scope(MarketResearchRun.owner_id, owner_id),
        )
    )
    if item is None:
        raise NotFoundError("市场探查记录不存在")
    return item


async def list_runs(
    session: AsyncSession, owner_id: int, limit: int = 12
) -> list[MarketResearchRun]:
    return list(
        (
            await session.scalars(
                select(MarketResearchRun)
                .where(owner_scope(MarketResearchRun.owner_id, owner_id))
                .order_by(MarketResearchRun.id.desc())
                .limit(limit)
            )
        ).all()
    )


async def create_run(
    session: AsyncSession, owner_id: int, data: dict[str, Any]
) -> tuple[MarketResearchRun, Job]:
    settings = await provider_service.get_ai_settings(session)
    if not settings.market_research_enabled:
        raise ConflictError("短剧市场探查已由管理员停用")
    if (
        settings.market_search_provider == "tavily"
        and not settings.market_search_api_key_ciphertext
    ):
        raise ConflictError("当前强制使用 Tavily，但尚未配置 API Key")
    model, route_source = await provider_service.resolve_agent_model(
        session, "market"
    )
    bound_skills = await provider_service.get_agent_skills(session, "market")
    market_skill = next((item for item in bound_skills if "market.search" in item.allowed_tools), None)
    active = await session.scalar(
        select(MarketResearchRun.id)
        .where(
            owner_scope(MarketResearchRun.owner_id, owner_id),
            MarketResearchRun.status.in_(RUN_ACTIVE),
        )
        .limit(1)
    )
    if active is not None:
        raise ConflictError("已有市场探查任务正在运行，请等待完成后再试")

    item = MarketResearchRun(owner_id=owner_id, **data, status="queued")
    session.add(item)
    await session.flush()
    job = await job_service.create_text_job(
        session,
        owner_id,
        provider_model_id=model.id,
        prompt="短剧市场探查任务：等待 Web 搜索结果。",
        project_id=None,
        parameters={
            "response_format": {"type": "json_object"},
            "market_research_run_id": item.id,
            "market": item.market,
            "time_range": item.time_range,
        },
    )
    job.target_type = TARGET_MARKET_RESEARCH
    job.target_id = item.id
    job.payload = {
        **job.payload,
        "agent_execution": {
            "agent": "market",
            "surface": "market_research",
            "route_source": route_source,
            "provider_model_id": model.id,
            "model_id": model.model_id,
            "instruction": settings.market_research_instruction,
            "skills": [
                {"id": skill.id, "key": skill.key, "name": skill.name, "version": skill.version,
                 "allowed_tools": list(skill.allowed_tools), "write_policy": skill.write_policy}
                for skill in bound_skills
            ],
            "skill_instruction": market_skill.instruction if market_skill is not None else None,
            "search_provider": settings.market_search_provider,
            "search_max_results": settings.market_search_max_results,
            "search_timeout_seconds": settings.market_search_timeout_seconds,
        },
    }
    item.job_id = job.id
    await session.flush()
    return item, job


async def select_idea(
    session: AsyncSession, run_id: int, owner_id: int, idea_index: int
) -> MarketResearchRun:
    item = await get_run(session, run_id, owner_id)
    ideas = (item.report or {}).get("ideas", [])
    if item.status != "succeeded" or not isinstance(ideas, list):
        raise ConflictError("市场探查尚未完成，不能带入剧本创作")
    if idea_index < 0 or idea_index >= len(ideas):
        raise NotFoundError("市场创意不存在")
    adopted = await session.scalar(
        select(MarketIdeaProject.id).where(
            MarketIdeaProject.market_research_run_id == item.id,
            MarketIdeaProject.idea_index == idea_index,
        )
    )
    if adopted is not None:
        raise ConflictError("该市场创意已创建项目，请从项目列表继续创作")
    item.selected_idea_index = idea_index
    await session.flush()
    return item


async def rerun(session: AsyncSession, run_id: int, owner_id: int) -> tuple[MarketResearchRun, Job]:
    source = await get_run(session, run_id, owner_id)
    return await create_run(
        session,
        owner_id,
        {
            "market": source.market,
            "region": source.region,
            "platforms": list(source.platforms),
            "genres": list(source.genres),
            "audience": source.audience,
            "time_range": source.time_range,
            "keywords": source.keywords,
        },
    )


async def delete_runs(
    session: AsyncSession, owner_id: int, run_ids: list[int]
) -> list[int]:
    ids = list(dict.fromkeys(run_ids))
    items = list(
        (
            await session.scalars(
                select(MarketResearchRun).where(
                    owner_scope(MarketResearchRun.owner_id, owner_id),
                    MarketResearchRun.id.in_(ids),
                )
            )
        ).all()
    )
    if len(items) != len(ids):
        raise NotFoundError("市场探索记录不存在")
    active = [item.id for item in items if item.status in RUN_ACTIVE]
    if active:
        raise ConflictError("运行中的探索记录不能删除，请等待任务完成")
    job_ids = [item.job_id for item in items if item.job_id is not None]
    if job_ids:
        jobs = list((await session.scalars(select(Job).where(Job.id.in_(job_ids)))).all())
        for job in jobs:
            if job.target_type == TARGET_MARKET_RESEARCH and await same_team(session, owner_id, job.owner_id):
                job.target_type = "market_research_deleted"
                job.target_id = None
    for item in items:
        await session.delete(item)
    await session.flush()
    return ids


def _query_for(item: MarketResearchRun) -> str:
    market = "中国国内短剧" if item.market == "domestic" else "overseas vertical short drama"
    parts = [
        market,
        "爆款 热门 趋势 流量 题材"
        if item.market == "domestic"
        else "trending hit series audience growth popular genres",
        item.region,
        " ".join(item.platforms),
        " ".join(item.genres),
        item.audience,
        item.keywords,
    ]
    return " ".join(part.strip() for part in parts if part and part.strip())


async def _tavily_search(
    *, api_key: str, query: str, start_date: str, timeout: int, max_results: int
) -> list[dict[str, Any]]:
    try:
        async with outbound_client(timeout=timeout) as client:
            response = await client.post(
                f"{TAVILY_BASE_URL}/search",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "search_depth": "basic",
                    "topic": "general",
                    "start_date": start_date,
                    "include_answer": False,
                    "include_raw_content": False,
                    "max_results": max_results,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            raise ProviderError("Tavily API Key 无效或已失效，请在系统设置中更新") from exc
        if exc.response.status_code == 429:
            raise ProviderError("Tavily 搜索额度已用尽或请求过于频繁，请稍后重试") from exc
        raise ProviderError(f"Tavily 搜索服务返回 HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError("Web 搜索连接失败，请检查网络或稍后重试") from exc
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise ProviderError("Web 搜索没有返回可用来源，请调整市场条件后重试")
    return results


async def prepare_prompt(job_id: int) -> str:
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None or job.target_type != TARGET_MARKET_RESEARCH or job.target_id is None:
            raise NotFoundError("市场探查任务不存在")
        item = await get_run(session, job.target_id, job.owner_id)
        settings = await provider_service.get_ai_settings(session)
        query = _query_for(item)
        start_date = (utcnow().date() - timedelta(days=int(item.time_range[:-1]))).isoformat()
        execution = dict(job.payload.get("agent_execution") or {})
        timeout = max(5, min(int(execution.get("search_timeout_seconds", settings.market_search_timeout_seconds)), 60))
        max_results = max(3, min(int(execution.get("search_max_results", settings.market_search_max_results)), 20))
        search_mode = str(execution.get("search_provider") or settings.market_search_provider or "auto")
        model = await session.get(ProviderModel, job.payload["provider_model_id"])
        provider = await session.get(Provider, model.provider_id) if model else None
        if model is None or provider is None:
            raise ConflictError("市场探查模型配置不存在")
        provider_key = decrypt_api_key(provider.api_key_ciphertext)
        tavily_key = (
            decrypt_api_key(settings.market_search_api_key_ciphertext)
            if settings.market_search_api_key_ciphertext
            else None
        )
        item.status = "processing"
        job.progress = 15
        await session.commit()

    results: list[dict[str, Any]] = []
    backend = "tavily"
    native_error: ProviderError | None = None
    if search_mode in {"auto", "native"}:
        try:
            adapter = create_provider_adapter(provider, provider_key, model)
            native = await adapter.web_search(
                model=model.model_id,
                query=query,
                max_results=max_results,
            )
            brief = str(native["text"])
            results = [{**source, "content": brief, "score": 1.0} for source in native["sources"]]
            backend = f"native:{native.get('tool_type', 'web_search')}"
        except (AttributeError, ProviderError) as exc:
            native_error = (
                exc
                if isinstance(exc, ProviderError)
                else ProviderError("该模型渠道不支持原生 Web Search")
            )
            if search_mode == "native":
                raise ProviderError(f"模型原生 Web Search 不可用：{native_error.message}") from exc

    if not results:
        if not tavily_key:
            detail = f"；原生探测失败：{native_error.message}" if native_error else ""
            raise ProviderError(f"模型不支持原生 Web Search，且未配置 Tavily 回退{detail}")
        results = await _tavily_search(
            api_key=tavily_key,
            query=query,
            start_date=start_date,
            timeout=timeout,
            max_results=max_results,
        )

    sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "").strip()
        title = str(result.get("title") or "").strip()
        content = str(result.get("content") or "").strip()
        if (
            not url.startswith(("https://", "http://"))
            or not title
            or not content
            or url in seen_urls
        ):
            continue
        seen_urls.add(url)
        sources.append(
            {
                "id": len(sources) + 1,
                "title": title[:300],
                "url": url[:1000],
                "domain": (urlparse(url).hostname or "")[:255],
                "snippet": content[:2000],
                "score": float(result.get("score") or 0),
                "backend": backend,
            }
        )
    if not sources:
        raise ProviderError("Web 搜索结果缺少有效来源")

    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None or job.target_id is None:
            raise NotFoundError("市场探查任务不存在")
        item = await get_run(session, job.target_id, job.owner_id)
        item.sources = sources
        job.progress = 45
        execution = dict(job.payload.get("agent_execution") or {})
        instruction = str(execution.get("instruction") or "基于真实来源生成可追溯结论。")
        skill_instruction = str(execution.get("skill_instruction") or "")
        await session.commit()

    return f"""你是短剧市场研究 Agent。只能基于给定 Web 搜索来源总结，不得编造播放量、收入或排名。
不确定的数字必须标注为公开信号或未核实；每个趋势和 Idea 必须引用来源编号。
只输出 JSON，不要 Markdown。结构必须为：
{{"summary":"", "trends":[{{"title":"","signal":"","evidence_source_ids":[1]}}],
"ideas":[{{"title":"","logline":"","hook":"","audience":"","recommended_format":"","why_now":"","evidence_source_ids":[1]}}],
"risks":[""]}}
生成 3-6 个差异化 Idea，避免照搬已有作品。系统规则：{instruction}
已绑定 Skill：{skill_instruction or '使用兼容模式，不附加额外 Skill 指令。'}
探查条件：市场={item.market}；地区={item.region}；平台={item.platforms}；类型={item.genres}；
受众={item.audience}；时间={item.time_range}；关键词={item.keywords}
搜索路径：{backend}
Web 来源：{json.dumps(sources, ensure_ascii=False)}"""


def _parse_report(text: str) -> dict[str, Any]:
    return parse_structured_result(text, MarketResearchReport, "市场分析结果")


async def finalize_run(session: AsyncSession, job: Job, result: dict[str, Any]) -> dict[str, Any]:
    if job.target_id is None:
        raise NotFoundError("市场探查任务不存在")
    item = await get_run(session, job.target_id, job.owner_id)
    report = _parse_report(str(result.get("text") or ""))
    valid_ids = {int(source["id"]) for source in item.sources}
    for entry in [*report["trends"], *report["ideas"]]:
        evidence = {int(value) for value in entry["evidence_source_ids"]}
        if not evidence or not evidence.issubset(valid_ids):
            raise ProviderError("市场分析结果引用了不存在的 Web 来源")
    item.report = report
    item.status = "succeeded"
    item.error_message = None
    await session.flush()
    return {**result, "market_research_run_id": item.id, "report": report, "sources": item.sources}


async def mark_failed(session: AsyncSession, job: Job) -> None:
    if job.target_type != TARGET_MARKET_RESEARCH or job.target_id is None:
        return
    item = await session.get(MarketResearchRun, job.target_id)
    if item is not None and item.owner_id == job.owner_id:
        item.status = "failed"
        item.error_message = (job.error_message or "市场探查失败，请调整条件后重试")[:2000]
        await session.flush()
