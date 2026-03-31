from pydantic import BaseModel, Field


class ProgressRequest(BaseModel):
    script: str = Field(..., min_length=1, max_length=120000)
    recognized_text: str = Field(..., min_length=1, max_length=4000)


class ProgressResponse(BaseModel):
    matched_index: int
    matched_preview: str
    confidence: float


class SettingsResponse(BaseModel):
    font_size: int
    line_height: float
    scroll_speed: float
    content_width: int
    theme: str
    font_family: str
