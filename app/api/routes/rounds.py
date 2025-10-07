from fastapi import APIRouter, Depends, HTTPException, status
from typing import Any
import logging

from app.api.deps import api_key_auth
from app.services.idempotency import idempotency_guard
from app.db.mongo import get_db
from app.models.schemas import (
    RoundHeader, EventRecord, TaskRunBatch, AgentRunUpsert,
    ProgressPayload, WeightsPut, RoundSummary, RoundResults,
    SuccessResponse
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/rounds", tags=["rounds"])


def ok_response(**extras) -> SuccessResponse:
    """Helper function to create success responses."""
    return SuccessResponse(data=extras)


# -------------- Round Management Endpoints --------------

@router.post("/start", response_model=SuccessResponse)
async def start_round(
    payload: RoundHeader, 
    token: str = Depends(api_key_auth), 
    idem=Depends(idempotency_guard)
):
    """Start a new round or update existing round header."""
    db = get_db()
    doc = payload.model_dump()
    
    try:
        result = await db.rounds.update_one(
            {"validator_uid": doc["validator_uid"], "round_id": doc["round_id"]},
            {"$setOnInsert": doc},
            upsert=True
        )
        
        if result.upserted_id:
            logger.info(f"Created new round: {payload.round_id} for validator {payload.validator_uid}")
        else:
            logger.info(f"Round already exists: {payload.round_id} for validator {payload.validator_uid}")
        
        return ok_response(round_id=payload.round_id, created=result.upserted_id is not None)
        
    except Exception as e:
        logger.error(f"Error starting round {payload.round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start round: {str(e)}")


@router.post("/{round_id}/events", response_model=SuccessResponse)
async def post_event(
    round_id: str, 
    payload: EventRecord, 
    token: str = Depends(api_key_auth), 
    idem=Depends(idempotency_guard)
):
    """Post an event for a specific round."""
    db = get_db()
    doc = payload.model_dump()
    
    if doc["round_id"] != round_id:
        raise HTTPException(
            status_code=400, 
            detail="round_id in URL does not match payload"
        )
    
    try:
        result = await db.events.insert_one(doc)
        logger.info(f"Event posted for round {round_id}: {payload.phase}")
        return ok_response(event_id=str(result.inserted_id))
        
    except Exception as e:
        logger.error(f"Error posting event for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to post event: {str(e)}")


@router.post("/{round_id}/task-runs:batch-upsert", response_model=SuccessResponse)
async def upsert_task_runs(
    round_id: str, 
    payload: TaskRunBatch, 
    token: str = Depends(api_key_auth)
):
    """Batch upsert task runs for a specific round."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(
            status_code=400, 
            detail="round_id in URL does not match payload"
        )
    
    upserted = 0
    errors = []
    
    try:
        for tr in payload.task_runs:
            doc = tr.model_dump()
            try:
                await db.task_runs.update_one(
                    {
                        "validator_uid": doc["validator_uid"],
                        "round_id": doc["round_id"],
                        "task_id": doc["task_id"],
                        "miner_uid": doc["miner_uid"],
                    },
                    {"$set": doc},
                    upsert=True
                )
                upserted += 1
            except Exception as e:
                errors.append(f"Task {doc['task_id']} for miner {doc['miner_uid']}: {str(e)}")
        
        logger.info(f"Upserted {upserted} task runs for round {round_id}")
        return ok_response(upserted=upserted, errors=errors if errors else None)
        
    except Exception as e:
        logger.error(f"Error upserting task runs for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upsert task runs: {str(e)}")


@router.post("/{round_id}/agent-runs:upsert", response_model=SuccessResponse)
async def upsert_agent_runs(
    round_id: str, 
    payload: AgentRunUpsert, 
    token: str = Depends(api_key_auth)
):
    """Upsert agent runs for a specific round."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(
            status_code=400, 
            detail="round_id in URL does not match payload"
        )
    
    upserted = 0
    errors = []
    
    try:
        for ar in payload.agent_runs:
            doc = ar.model_dump()
            try:
                await db.agent_runs.update_one(
                    {
                        "validator_uid": doc["validator_uid"],
                        "round_id": doc["round_id"],
                        "miner_uid": doc["miner_uid"],
                    },
                    {"$set": doc},
                    upsert=True
                )
                upserted += 1
            except Exception as e:
                errors.append(f"Agent run for miner {doc['miner_uid']}: {str(e)}")
        
        logger.info(f"Upserted {upserted} agent runs for round {round_id}")
        return ok_response(upserted=upserted, errors=errors if errors else None)
        
    except Exception as e:
        logger.error(f"Error upserting agent runs for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upsert agent runs: {str(e)}")


@router.post("/{round_id}/progress", response_model=SuccessResponse)
async def post_progress(
    round_id: str, 
    payload: ProgressPayload, 
    token: str = Depends(api_key_auth)
):
    """Post progress update for a specific round."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(
            status_code=400, 
            detail="round_id in URL does not match payload"
        )
    
    try:
        # Store as an event
        event_doc = {
            "validator_uid": payload.validator_uid,
            "round_id": payload.round_id,
            "phase": "progress",
            "message": f"{payload.tasks_completed}/{payload.tasks_total}",
            "ts": __import__("time").time(),
            "extra": payload.extra
        }
        
        result = await db.events.insert_one(event_doc)
        logger.info(f"Progress posted for round {round_id}: {payload.tasks_completed}/{payload.tasks_total}")
        return ok_response(event_id=str(result.inserted_id))
        
    except Exception as e:
        logger.error(f"Error posting progress for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to post progress: {str(e)}")


@router.put("/{round_id}/weights", response_model=SuccessResponse)
async def put_weights(
    round_id: str, 
    payload: WeightsPut, 
    token: str = Depends(api_key_auth)
):
    """Update weights for a specific round."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(
            status_code=400, 
            detail="round_id in URL does not match payload"
        )
    
    try:
        doc = payload.model_dump()
        weights_doc = doc["weights"].copy()
        weights_doc.update({
            "validator_uid": doc["validator_uid"],
            "round_id": doc["round_id"]
        })
        
        await db.weights.update_one(
            {"validator_uid": doc["validator_uid"], "round_id": doc["round_id"]},
            {"$set": weights_doc},
            upsert=True
        )
        
        logger.info(f"Weights updated for round {round_id}")
        return ok_response(round_id=round_id)
        
    except Exception as e:
        logger.error(f"Error updating weights for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update weights: {str(e)}")


@router.post("/{round_id}/finalize", response_model=SuccessResponse)
async def finalize_round(
    round_id: str, 
    payload: RoundSummary, 
    token: str = Depends(api_key_auth)
):
    """Finalize a round with summary data."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(
            status_code=400, 
            detail="round_id in URL does not match payload"
        )
    
    try:
        doc = payload.model_dump()
        
        # Update the round with finalization data
        await db.rounds.update_one(
            {"validator_uid": doc["validator_uid"], "round_id": doc["round_id"]},
            {"$set": {
                "ended_at": doc["ended_at"],
                "elapsed_sec": doc["elapsed_sec"],
                "n_active_miners": doc["n_active_miners"],
                "state": "finalized",
                "stats": doc["stats"],
                "meta": doc["meta"],
            }},
            upsert=True
        )
        
        logger.info(f"Round {round_id} finalized")
        return ok_response(round_id=round_id, state="finalized")
        
    except Exception as e:
        logger.error(f"Error finalizing round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to finalize round: {str(e)}")


@router.post("/{round_id}/round-results", response_model=SuccessResponse)
async def post_round_results(
    round_id: str, 
    payload: RoundResults, 
    token: str = Depends(api_key_auth)
):
    """Post complete round results for archival."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(
            status_code=400, 
            detail="round_id in URL does not match payload"
        )
    
    try:
        doc = payload.model_dump()
        
        await db.round_results.update_one(
            {"validator_uid": doc["validator_uid"], "round_id": doc["round_id"]},
            {"$set": doc},
            upsert=True
        )
        
        logger.info(f"Round results posted for round {round_id}")
        return ok_response(round_id=round_id)
        
    except Exception as e:
        logger.error(f"Error posting round results for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to post round results: {str(e)}")


# -------------- Optional GET endpoints for UI/ops --------------

@router.get("/{round_id}/status", response_model=SuccessResponse)
async def get_round_status(
    round_id: str,
    validator_uid: int,
    token: str = Depends(api_key_auth)
):
    """Get the current status of a round."""
    db = get_db()
    
    try:
        round_doc = await db.rounds.find_one({
            "validator_uid": validator_uid,
            "round_id": round_id
        })
        
        if not round_doc:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Get recent events
        events = await db.events.find({
            "validator_uid": validator_uid,
            "round_id": round_id
        }).sort("ts", -1).limit(10).to_list(10)
        
        # Get task run count
        task_count = await db.task_runs.count_documents({
            "validator_uid": validator_uid,
            "round_id": round_id
        })
        
        # Get agent run count
        agent_count = await db.agent_runs.count_documents({
            "validator_uid": validator_uid,
            "round_id": round_id
        })
        
        return ok_response(
            round=round_doc,
            recent_events=events,
            task_runs_count=task_count,
            agent_runs_count=agent_count
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting round status for {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get round status: {str(e)}")


@router.get("/{round_id}/weights", response_model=SuccessResponse)
async def get_round_weights(
    round_id: str,
    validator_uid: int,
    token: str = Depends(api_key_auth)
):
    """Get weights for a specific round."""
    db = get_db()
    
    try:
        weights_doc = await db.weights.find_one({
            "validator_uid": validator_uid,
            "round_id": round_id
        })
        
        if not weights_doc:
            raise HTTPException(status_code=404, detail="Weights not found")
        
        return ok_response(weights=weights_doc)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting weights for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get weights: {str(e)}")
