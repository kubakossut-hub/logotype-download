from pydantic import BaseModel
from typing import Optional


class SearchRequest(BaseModel):
    companies: list[str]
    context: str = ""


class Candidate(BaseModel):
    id: str
    url: str
    source: str
    label: str


class CompanyResult(BaseModel):
    company: str
    domain_guess: str
    candidates: list[Candidate]


class SearchResponse(BaseModel):
    results: list[CompanyResult]


class AssessItem(BaseModel):
    candidate_id: str
    company: str
    url: str


class AssessRequest(BaseModel):
    items: list[AssessItem]


class Assessment(BaseModel):
    candidate_id: str
    company: str
    url: str
    reachable: bool
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None
    has_transparency: bool = False
    file_size_kb: Optional[float] = None
    quality_score: int = 0
    quality_label: str = "Bad"
    thumbnail_b64: Optional[str] = None


class AssessResponse(BaseModel):
    assessments: list[Assessment]


class ExportItem(BaseModel):
    company: str
    url: str


class ExportRequest(BaseModel):
    selections: list[ExportItem]
    logo_width_cm: float = 5.0
    show_labels: bool = True
