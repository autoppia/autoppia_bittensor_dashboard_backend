#!/usr/bin/env python3
"""
Debug script to understand the validation failures.
"""

import os
import sys
import asyncio
from pathlib import Path

# Add the app directory to the path
sys.path.append(str(Path(__file__).parent.parent.parent))

# Import the generator directly
sys.path.append(str(Path(__file__).parent.parent / "data_generation"))
from generate_test_data_new import NewTestDataGenerator
from app.models.schemas import RoundSubmissionRequest


async def main():
    """Debug the validation failures."""
    print("🐛 Debug Validation Failures")
    print("=" * 50)
    
    # Set environment variable for mock mode
    os.environ["USE_MOCK_DB"] = "true"
    
    generator = NewTestDataGenerator()
    
    # Generate a single submission
    print("📊 Generating test data...")
    submissions = await generator.generate_all_data(num_rounds=1)
    
    if not submissions:
        print("❌ No test data generated!")
        return
    
    submission = submissions[0]
    print(f"✅ Generated submission for round: {submission.round.round_id}")
    
    # Debug the structure
    print(f"\n🔍 Debugging structure...")
    print(f"   Round ID: {submission.round.round_id}")
    print(f"   Validator UID: {submission.round.validator_info.uid}")
    print(f"   Miners: {len(submission.round.miners)}")
    print(f"   Agent Runs: {len(submission.agent_evaluation_runs)}")
    print(f"   Tasks: {len(submission.tasks)}")
    print(f"   Task Solutions: {len(submission.task_solutions)}")
    print(f"   Evaluation Results: {len(submission.evaluation_results)}")
    
    # Check task IDs
    print(f"\n📋 Task IDs:")
    task_ids = set()
    for i, task in enumerate(submission.tasks[:10]):  # Show first 10
        print(f"   Task {i+1}: {task.task_id} (agent_run_id: {task.agent_run_id})")
        task_ids.add(task.task_id)
    
    print(f"   Total unique task IDs: {len(task_ids)}")
    print(f"   Total tasks: {len(submission.tasks)}")
    
    # Check task solution IDs
    print(f"\n📋 Task Solution IDs:")
    solution_ids = set()
    for i, task_solution in enumerate(submission.task_solutions[:10]):  # Show first 10
        print(f"   Solution {i+1}: {task_solution.solution_id} (task_id: {task_solution.task_id}, agent_run_id: {task_solution.agent_run_id})")
        solution_ids.add(task_solution.solution_id)
    
    print(f"   Total unique solution IDs: {len(solution_ids)}")
    print(f"   Total task solutions: {len(submission.task_solutions)}")
    
    # Check evaluation result IDs
    print(f"\n📋 Evaluation Result IDs:")
    eval_ids = set()
    for i, eval_result in enumerate(submission.evaluation_results[:10]):  # Show first 10
        print(f"   Eval {i+1}: {eval_result.evaluation_id} (task_id: {eval_result.task_id}, task_solution_id: {eval_result.task_solution_id}, agent_run_id: {eval_result.agent_run_id})")
        eval_ids.add(eval_result.evaluation_id)
    
    print(f"   Total unique eval IDs: {len(eval_ids)}")
    print(f"   Total evaluation results: {len(submission.evaluation_results)}")
    
    # Check agent runs
    print(f"\n👥 Agent Runs:")
    for i, agent_run in enumerate(submission.agent_evaluation_runs):
        print(f"   Agent Run {i+1}: {agent_run.agent_run_id} (miner_uid: {agent_run.miner_uid})")
        
        # Count tasks for this agent run
        agent_tasks = [task for task in submission.tasks if task.agent_run_id == agent_run.agent_run_id]
        print(f"     Tasks: {len(agent_tasks)}")
        
        # Count task solutions for this agent run
        agent_solutions = [ts for ts in submission.task_solutions if ts.agent_run_id == agent_run.agent_run_id]
        print(f"     Task Solutions: {len(agent_solutions)}")
        
        # Count evaluation results for this agent run
        agent_evals = [er for er in submission.evaluation_results if er.agent_run_id == agent_run.agent_run_id]
        print(f"     Evaluation Results: {len(agent_evals)}")
    
    # Test a specific validation
    print(f"\n🔍 Testing specific validation...")
    
    # Test first task solution
    if submission.task_solutions:
        task_solution = submission.task_solutions[0]
        print(f"   Testing task solution: {task_solution.solution_id}")
        print(f"     Task ID: {task_solution.task_id}")
        print(f"     Agent Run ID: {task_solution.agent_run_id}")
        
        # Find corresponding task
        task = next((t for t in submission.tasks if t.task_id == task_solution.task_id), None)
        if task:
            print(f"     Found task: {task.task_id} (agent_run_id: {task.agent_run_id})")
        else:
            print(f"     ❌ Task not found!")
        
        # Find corresponding agent run
        agent_run = next((ar for ar in submission.agent_evaluation_runs if ar.agent_run_id == task_solution.agent_run_id), None)
        if agent_run:
            print(f"     Found agent run: {agent_run.agent_run_id} (miner_uid: {agent_run.miner_uid})")
        else:
            print(f"     ❌ Agent run not found!")
        
        # Test validation
        if task and agent_run:
            try:
                result = task_solution.validate_relationships(agent_run, task)
                print(f"     Validation result: {result}")
            except Exception as e:
                print(f"     ❌ Validation error: {e}")
                import traceback
                traceback.print_exc()
    
    print(f"\n✅ Debug completed!")


if __name__ == "__main__":
    asyncio.run(main())
