from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.services.ai.intent_service import intent_service, IntentResponse
from app.core.dependencies import require_api_key

router = APIRouter()

class ChatRequest(BaseModel):
    content: str

@router.post("/intent", response_model=IntentResponse)
async def predict_intent(request: ChatRequest, _=Depends(require_api_key)):
    """
    Predict the intent of a user message.
    """
    if not request.content:
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    
    result = await intent_service.identify_intent(request.content)
    return result
