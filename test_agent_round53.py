import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from app.db.session import AsyncSessionLocal
from sqlalchemy import select
from app.db.models import AgentStatsORM, RoundSnapshotORM

async def test():
    async with AsyncSessionLocal() as session:
        # Get round 53 snapshot
        snapshot = await session.get(RoundSnapshotORM, 53)
        if not snapshot:
            print("❌ Round 53 not found")
            return
        
        snapshot_json = snapshot.snapshot_json
        validator_rounds = snapshot_json.get("validatorRounds", [])
        
        print(f"Round 53 has {len(validator_rounds)} validatorRounds")
        
        # Find agent 105 in the snapshot
        agent_uid = 105
        found_in_validators = []
        
        for vr in validator_rounds:
            validator_uid = vr.get("validatorUid")
            agent_runs = vr.get("agentEvaluationRuns", [])
            
            for run in agent_runs:
                if run.get("miner_uid") == agent_uid:
                    found_in_validators.append({
                        "validator": validator_uid,
                        "score": run.get("average_score", 0),
                        "rank": run.get("rank"),
                        "tasks": run.get("total_tasks", 0)
                    })
        
        print(f"\nAgent {agent_uid} found in {len(found_in_validators)} validators:")
        for data in found_in_validators:
            print(f"  Validator {data['validator']}: score={data['score']:.3f}, rank={data['rank']}, tasks={data['tasks']}")

asyncio.run(test())
