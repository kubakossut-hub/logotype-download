from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from api.models.schemas import ExportRequest
from services.exporter import build_zip, build_pptx

router = APIRouter()


@router.post("/zip")
async def export_zip(request: ExportRequest):
    selections = [{"company": s.company, "url": s.url} for s in request.selections]
    buffer = await build_zip(selections)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=logos.zip"},
    )


@router.post("/pptx")
async def export_pptx(request: ExportRequest):
    selections = [{"company": s.company, "url": s.url} for s in request.selections]
    buffer = await build_pptx(
        selections,
        logo_width_cm=request.logo_width_cm,
        show_labels=request.show_labels,
    )
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": "attachment; filename=logos.pptx"},
    )
