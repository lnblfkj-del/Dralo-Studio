"""Canonical definitions for the product's professional Agents."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    key: str
    name: str
    description: str
    skill_mode: str
    supported_modalities: tuple[str, ...]
    route_fields: tuple[tuple[str, str], ...]
    execution_surfaces: tuple[str, ...]
    supports_skill: bool = True

    def to_dict(self) -> dict:
        value = asdict(self)
        value["supported_modalities"] = list(self.supported_modalities)
        value["route_fields"] = dict(self.route_fields)
        value["execution_surfaces"] = list(self.execution_surfaces)
        return value


AGENT_DEFINITIONS: tuple[AgentDefinition, ...] = (
    AgentDefinition(
        key="outline",
        name="大纲 Agent",
        description="理解故事设定、事件线与分集规划，按固定 Skill 维护项目结构。",
        skill_mode="outline",
        supported_modalities=("text",),
        route_fields=(("text", "outline_agent_text_model_id"),),
        execution_surfaces=("project_outline", "story_bible"),
    ),
    AgentDefinition(
        key="script",
        name="剧本 Agent",
        description="根据一句话创意或参考材料创建并改写可分集执行的短剧剧本。",
        skill_mode="script",
        supported_modalities=("text",),
        route_fields=(("text", "script_agent_text_model_id"),),
        execution_surfaces=("creation_entry", "episode_script"),
    ),
    AgentDefinition(
        key="canvas",
        name="画布 Agent",
        description="管理画布节点和连接，生成文本、图片、视频与音频素材。",
        skill_mode="canvas",
        supported_modalities=("text", "image", "video", "audio", "tts"),
        route_fields=(
            ("text", "canvas_agent_text_model_id"),
            ("image", "canvas_agent_image_model_id"),
            ("video", "canvas_agent_video_model_id"),
            ("audio", "canvas_agent_audio_model_id"),
            ("tts", "canvas_agent_tts_model_id"),
        ),
        execution_surfaces=("infinite_canvas",),
    ),
    AgentDefinition(
        key="market",
        name="市场探查 Agent",
        description="检索真实市场信号、汇总证据并产出可采用的选题与趋势判断。",
        skill_mode="market",
        supported_modalities=("text",),
        route_fields=(("text", "market_research_model_id"),),
        execution_surfaces=("market_research",),
        supports_skill=True,
    ),
)


def list_agent_definitions() -> list[dict]:
    return [definition.to_dict() for definition in AGENT_DEFINITIONS]


def get_agent_definition(key: str) -> AgentDefinition:
    for definition in AGENT_DEFINITIONS:
        if definition.key == key:
            return definition
    raise KeyError(key)
