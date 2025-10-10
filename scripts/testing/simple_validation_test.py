#!/usr/bin/env python3
"""
Simple test to isolate the validation issue.
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
    """Simple test to isolate the validation issue."""
    print("🧪 Simple Validation Test")
    print("=" * 40)
    
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
    
    # Test individual components
    print("\n🔍 Testing individual components...")
    
    # Test round
    print(f"   Round ID: {submission.round.round_id}")
    print(f"   Validator UID: {submission.round.validator_info.uid}")
    print(f"   Miners: {len(submission.round.miners)}")
    
    # Test agent runs
    print(f"   Agent Runs: {len(submission.agent_evaluation_runs)}")
    for i, agent_run in enumerate(submission.agent_evaluation_runs[:2]):  # Test first 2
        print(f"     Agent Run {i+1}: {agent_run.agent_run_id}")
        print(f"       Validator UID: {agent_run.validator_uid}")
        print(f"       Miner UID: {agent_run.miner_uid}")
    
    # Test tasks
    print(f"   Tasks: {len(submission.tasks)}")
    for i, task in enumerate(submission.tasks[:2]):  # Test first 2
        print(f"     Task {i+1}: {task.task_id}")
        print(f"       Round ID: {task.round_id}")
        print(f"       Agent Run ID: {task.agent_run_id}")
        # This should work - Task doesn't have validator_uid
        print(f"       Task has validator_uid: {hasattr(task, 'validator_uid')}")
    
    # Test task solutions
    print(f"   Task Solutions: {len(submission.task_solutions)}")
    for i, task_solution in enumerate(submission.task_solutions[:2]):  # Test first 2
        print(f"     Task Solution {i+1}: {task_solution.solution_id}")
        print(f"       Task ID: {task_solution.task_id}")
        print(f"       Validator UID: {task_solution.validator_uid}")
        print(f"       Agent Run ID: {task_solution.agent_run_id}")
    
    # Test evaluation results
    print(f"   Evaluation Results: {len(submission.evaluation_results)}")
    for i, eval_result in enumerate(submission.evaluation_results[:2]):  # Test first 2
        print(f"     Evaluation Result {i+1}: {eval_result.evaluation_id}")
        print(f"       Task ID: {eval_result.task_id}")
        print(f"       Validator UID: {eval_result.validator_uid}")
        print(f"       Agent Run ID: {eval_result.agent_run_id}")
    
    # Test model validation methods
    print("\n🔍 Testing model validation methods...")
    
    try:
        # Test agent run validation
        agent_run = submission.agent_evaluation_runs[0]
        agent_tasks = [task for task in submission.tasks if task.agent_run_id == agent_run.agent_run_id]
        print(f"   Agent Run {agent_run.agent_run_id} has {len(agent_tasks)} tasks")
        
        result = agent_run.validate_task_relationships(agent_tasks)
        print(f"   Agent run validation result: {result}")
        
    except Exception as e:
        print(f"   ❌ Agent run validation error: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        # Test task solution validation
        task_solution = submission.task_solutions[0]
        task = next((t for t in submission.tasks if t.task_id == task_solution.task_id), None)
        agent_run = next((ar for ar in submission.agent_evaluation_runs if ar.agent_run_id == task_solution.agent_run_id), None)
        
        if task and agent_run:
            print(f"   Task Solution {task_solution.solution_id} validation...")
            result = task_solution.validate_relationships(agent_run, task)
            print(f"   Task solution validation result: {result}")
        else:
            print(f"   ❌ Could not find task or agent run for task solution")
            
    except Exception as e:
        print(f"   ❌ Task solution validation error: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        # Test evaluation result validation
        eval_result = submission.evaluation_results[0]
        task = next((t for t in submission.tasks if t.task_id == eval_result.task_id), None)
        task_solution = next((ts for ts in submission.task_solutions if ts.solution_id == eval_result.task_solution_id), None)
        agent_run = next((ar for ar in submission.agent_evaluation_runs if ar.agent_run_id == eval_result.agent_run_id), None)
        
        if task and task_solution and agent_run:
            print(f"   Evaluation Result {eval_result.evaluation_id} validation...")
            result = eval_result.validate_relationships(agent_run, task, task_solution)
            print(f"   Evaluation result validation result: {result}")
        else:
            print(f"   ❌ Could not find task, task solution, or agent run for evaluation result")
            
    except Exception as e:
        print(f"   ❌ Evaluation result validation error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n✅ Simple validation test completed!")


if __name__ == "__main__":
    asyncio.run(main())
