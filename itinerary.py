"""Structured daily itineraries."""

from datetime import date, time
from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class Coordinates(BaseModel):
    """Amap GCJ-02 coordinates."""

    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class Link(BaseModel):
    title: str
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        # Keep checkpoint values serializable while validating HTTP(S) links.
        return str(HttpUrl(value))


class Activity(BaseModel):
    id: str
    start_time: time
    end_time: time
    activity: str
    description: str = ""
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False,
                               description="同行人员合计预计费用，人民币元；未知为空，免费为0")
    links: list[Link] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_time(self):
        if self.end_time <= self.start_time:
            raise ValueError("结束时间必须晚于开始时间；跨午夜活动按日期拆分")
        return self


class PlaceStop(Activity):
    kind: Literal["place"] = "place"
    place_name: str
    coordinates: Coordinates | None = None


class TravelRoute(Activity):
    kind: Literal["route"] = "route"
    from_stop_id: str
    to_stop_id: str
    transport_modes: list[Literal[
        "walking", "subway", "bus", "taxi", "driving", "cycling", "train", "flight", "ferry",
    ]] = Field(min_length=1)


ItineraryItem = Annotated[PlaceStop | TravelRoute, Field(discriminator="kind")]


class DayItinerary(BaseModel):
    date: date
    title: str
    items: list[ItineraryItem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sequence(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("同一天的活动ID不能重复")
        for index, item in enumerate(self.items):
            if index and item.start_time < self.items[index - 1].end_time:
                raise ValueError("活动必须按时间排序且不能重叠")
            if index % 2 == 0:
                if not isinstance(item, PlaceStop):
                    raise ValueError("行程必须按地点、路线、地点的顺序排列")
            else:
                if not isinstance(item, TravelRoute) or index == len(self.items) - 1:
                    raise ValueError("两个相邻地点之间必须有一条路线")
                if (item.from_stop_id != self.items[index - 1].id
                        or item.to_stop_id != self.items[index + 1].id):
                    raise ValueError("路线必须连接前后两个地点的ID")
        return self


class Itinerary(BaseModel):
    """完整的国内旅行行程；局部修改后仍返回所有天数的最新行程。"""

    title: str
    travelers: int = Field(ge=1)
    days: list[DayItinerary] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dates(self):
        dates = [day.date for day in self.days]
        if dates != sorted(set(dates)):
            raise ValueError("每天的日期必须唯一且按顺序排列")
        return self

