from fastapi import APIRouter
from api.models.schemas import SearchRequest, SearchResponse, AssessRequest, AssessResponse
from services.search import generate_all
from services.downloader import assess_all

router = APIRouter()


@router.post("/search", response_model=SearchResponse)
async def search_logos(request: SearchRequest):
    results = await generate_all(request.companies, context=request.context)
    return {"results": results}


@router.post("/assess", response_model=AssessResponse)
async def assess_logos(request: AssessRequest):
    items = [
        {"candidate_id": item.candidate_id, "company": item.company, "url": item.url}
        for item in request.items
    ]
    assessments = await assess_all(items)
    return {"assessments": assessments}
