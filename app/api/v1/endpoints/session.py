import uuid

from fastapi import APIRouter, Request

from app.schemas.session import SessionStartRequest, SessionStartResponse

router = APIRouter()


@router.post("/start", response_model=SessionStartResponse)
async def start_session(body: SessionStartRequest, request: Request) -> SessionStartResponse:
    thread_id = str(uuid.uuid4())
    graph = request.app.state.graph
    await graph.ainvoke(
        {"process_type": body.process_type},
        config={"configurable": {"thread_id": thread_id}},
    )
    return SessionStartResponse(thread_id=thread_id, process_type=body.process_type)
