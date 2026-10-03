"""故事设定相关的创作 Schema。"""
import math
import re
from app.core.creation_limits import MAX_EPISODES, MAX_STORY_CHARACTERS

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator


class EpisodeRange(BaseModel):
    start: int = Field(ge=1, le=MAX_EPISODES)
    end: int = Field(ge=1, le=MAX_EPISODES)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("出场范围结束集不能早于开始集")
        return self


class StoryBibleCharacter(BaseModel):
    model_config = ConfigDict(extra="allow")
    character_id: str | None = Field(default=None, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    age: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    personality: str | None = Field(default=None, max_length=2000)
    appearance: str | None = Field(default=None, max_length=4000)
    costume: str | None = Field(default=None, max_length=4000)
    voice: str | None = Field(default=None, max_length=2000)
    importance: Literal["core", "recurring", "phase", "functional"] | None = None
    narrative_function: str | None = Field(default=None, max_length=500)
    appearance_scope: str | None = Field(default=None, max_length=500)
    appearance_ranges: list[EpisodeRange] = Field(default_factory=list, max_length=MAX_EPISODES)
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=100)
    goal: str = Field(default="", max_length=1000)
    conflict: str = Field(default="", max_length=1000)
    arc: str = Field(default="", max_length=2000)

    @field_validator("age", mode="before")
    @classmethod
    def normalize_age(cls, value: Any) -> Any:
        # Providers commonly emit numeric ages; storage also supports age descriptions.
        if isinstance(value, bool):
            raise ValueError("年龄不能是布尔值")
        if isinstance(value, (int, float)):
            if value < 0 or (isinstance(value, float) and not math.isfinite(value)):
                raise ValueError("年龄必须是非负有限数值")
            return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
        return value

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        data = handler(self)
        if "appearance_ranges" not in self.model_fields_set:
            data.pop("appearance_ranges", None)
        for key in ("character_id", "aliases", "age", "description", "personality", "appearance", "costume", "voice", "importance", "narrative_function", "appearance_scope"):
            if key not in self.model_fields_set:
                data.pop(key, None)
        return data

    @model_validator(mode="before")
    @classmethod
    def normalize_provider_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        aliases = {
            "name": ("character", "character_name"),
            "role": ("identity", "position", "archetype", "description"),
            "goal": ("motivation", "objective", "desire"),
            "conflict": ("obstacle", "challenge"),
            "arc": ("development", "character_arc"),
            "importance": ("tier", "character_tier", "importance_level"),
            "narrative_function": ("story_function", "dramatic_function", "function"),
            "appearance_scope": ("scope", "episode_scope", "appearance_range"),
        }
        for target, sources in aliases.items():
            if normalized.get(target) not in (None, ""):
                continue
            replacement = next((normalized.get(source) for source in sources if normalized.get(source) not in (None, "")), None)
            if replacement is not None:
                normalized[target] = replacement
        tier_aliases = {"核心": "core", "主角": "core", "主要": "core", "lead": "core", "main": "core", "常驻": "recurring", "重要配角": "recurring", "supporting": "recurring", "阶段": "phase", "阶段反派": "phase", "arc": "phase", "功能": "functional", "临时": "functional", "客串": "functional", "cameo": "functional"}
        tier = str(normalized.get("importance") or "").strip().lower()
        if tier in tier_aliases:
            normalized["importance"] = tier_aliases[tier]
        return normalized


class StoryEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    event_id: str | None = Field(default=None, min_length=1, max_length=100)
    character_ids: list[str] = Field(default_factory=list, max_length=MAX_STORY_CHARACTERS)
    episode_range: EpisodeRange | None = None
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    episode_hint: int | None = Field(default=None, ge=1, le=MAX_EPISODES)

    @model_serializer(mode="wrap")
    def serialize_optional_references(self, handler):
        data = handler(self)
        for key in ("event_id", "character_ids", "episode_range"):
            if key not in self.model_fields_set:
                data.pop(key, None)
        return data

    @model_validator(mode="before")
    @classmethod
    def normalize_provider_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if normalized.get("title") in (None, ""):
            normalized["title"] = normalized.get("event") or normalized.get("name") or normalized.get("beat")
        if normalized.get("summary") in (None, ""):
            normalized["summary"] = normalized.get("description") or normalized.get("content") or normalized.get("event")
        if normalized.get("episode_hint") in (None, ""):
            normalized["episode_hint"] = normalized.get("episode") or normalized.get("episode_number")
        return normalized

    @field_validator("episode_hint", mode="before")
    @classmethod
    def normalize_episode_hint(cls, value: Any) -> Any:
        if value is None or isinstance(value, int):
            return value
        if isinstance(value, str):
            matched = re.match(r"\s*(?:(?:第|EP)\s*)?(\d+)\s*集?", value, re.IGNORECASE)
            if matched:
                return int(matched.group(1))
        return value


class StoryBibleContent(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = Field(min_length=1, max_length=255)
    logline: str = Field(min_length=1, max_length=1000)
    genre: str = Field(min_length=1, max_length=100)
    tone: str = Field(min_length=1, max_length=200)
    audience: str = Field(default="", max_length=200)
    world: str = Field(min_length=1, max_length=4000)
    themes: list[str] = Field(default_factory=list, max_length=12)
    characters: list[StoryBibleCharacter] = Field(min_length=1, max_length=MAX_STORY_CHARACTERS)
    event_timeline: list[StoryEvent] = Field(default_factory=list, max_length=MAX_EPISODES)
    character_ecosystem: dict[str, Any] | None = None

    @model_validator(mode="after")
    def valid_identities(self):
        identities = [row.character_id for row in self.characters if row.character_id]
        events = [row.event_id for row in self.event_timeline if row.event_id]
        if len(identities) != len(set(identities)) or len(events) != len(set(events)):
            raise ValueError("角色或事件身份编号重复")
        for event in self.event_timeline:
            if len(event.character_ids) != len(set(event.character_ids)):
                raise ValueError("事件的参与角色编号重复")
            if set(event.character_ids) - set(identities):
                raise ValueError("事件引用了不存在的角色编号")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        data = handler(self)
        if "character_ecosystem" not in self.model_fields_set:
            data.pop("character_ecosystem", None)
        return data

    @field_validator("tone", "audience", mode="before")
    @classmethod
    def normalize_text_list(cls, value: Any) -> Any:
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return " / ".join(item.strip() for item in value if item.strip())
        return value

    @field_validator("themes", mode="before")
    @classmethod
    def normalize_themes(cls, value: Any) -> Any:
        return [part.strip() for part in re.split(r"[,，、/]", value) if part.strip()] if isinstance(value, str) else value

    @field_validator("characters", mode="before")
    @classmethod
    def normalize_character_map(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return [{"name": name, **(details if isinstance(details, dict) else {"role": str(details)})} for name, details in value.items()]
        return value

    @field_validator("genre", mode="before")
    @classmethod
    def normalize_genre(cls, value: Any) -> Any:
        return " / ".join(item.strip() for item in value if item.strip()) if isinstance(value, list) and all(isinstance(item, str) for item in value) else value

    @field_validator("world", mode="before")
    @classmethod
    def normalize_world(cls, value: Any) -> Any:
        if isinstance(value, dict):
            parts = []
            for key, item in value.items():
                text = "、".join(str(entry) for entry in item) if isinstance(item, list) else "；".join(f"{nested_key}：{nested_value}" for nested_key, nested_value in item.items()) if isinstance(item, dict) else str(item)
                parts.append(f"{key}：{text}")
            return "\n".join(parts)
        return value
