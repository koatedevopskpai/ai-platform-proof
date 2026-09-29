from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models import Document
from app.services.embedding import embedding_service
from app.services.rag import rag_service
from app.services.retrieval import RetrievalService

router = APIRouter()


class IngestRequest(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    content: str = Field(min_length=1, max_length=100_000)


class IngestResponse(BaseModel):
    id: str
    dimensions: int


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4_000)
    top_k: int = Field(default=4, ge=1, le=20)


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "embedding_mode": settings.embedding_mode}


@router.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest, db: Session = Depends(get_db)) -> IngestResponse:
    if db.get(Document, req.id or ""):
        raise HTTPException(status_code=409, detail="document already exists")

    vector = embedding_service.embed(req.content)
    doc = Document(id=req.id, content=req.content, embedding=vector)
    db.add(doc)
    db.commit()
    return IngestResponse(id=doc.id, dimensions=len(vector))


@router.post("/search")
def search(req: QueryRequest, db: Session = Depends(get_db)) -> dict:
    qvec = embedding_service.embed(req.query)
    results = RetrievalService(db).search(qvec, top_k=req.top_k, query_text=req.query)
    return {"query": req.query, "results": results, "count": len(results)}


@router.post("/rag")
def rag(req: QueryRequest, db: Session = Depends(get_db)) -> dict:
    qvec = embedding_service.embed(req.query)
    contexts = RetrievalService(db).search(qvec, top_k=req.top_k, query_text=req.query)
    return rag_service.answer(req.query, contexts)