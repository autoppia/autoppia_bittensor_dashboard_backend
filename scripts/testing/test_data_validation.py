#!/usr/bin/env python3
"""
Test script to validate the generated data structure and models.
This script tests the data generation and model validation without requiring a running server.
"""

import os
import sys
import asyncio
import json
from typing import List, Dict, Any
from pathlib import Path

# Add the app directory to the path
sys.path.append(str(Path(__file__).parent.parent.parent))

# Import the generator directly
sys.path.append(str(Path(__file__).parent.parent / "data_generation"))
from generate_test_data_new import NewTestDataGenerator
from app.models.schemas import RoundSubmissionRequest


class DataValidationTester:
    """Test the data generation and model validation."""
    
    def __init__(self):
        self.generator = NewTestDataGenerator()
    
    def validate_submission_structure(self, submission: RoundSubmissionRequest) -> Dict[str, Any]:
        """Validate the structure of a round submission."""
        print(f"🔍 Validating submission structure...")
        print(f"   Round ID: {submission.round.round_id}")
        print(f"   Validator UID: {submission.round.validator_info.uid}")
        print(f"   Miners: {len(submission.round.miners)}")
        print(f"   Tasks: {len(submission.tasks)}")
        print(f"   Agent Runs: {len(submission.agent_evaluation_runs)}")
        print(f"   Task Solutions: {len(submission.task_solutions)}")
        print(f"   Evaluation Results: {len(submission.evaluation_results)}")
        
        # Validate relationships
        validation_results = {
            "round_valid": True,
            "agent_runs_valid": True,
            "tasks_valid": True,
            "task_solutions_valid": True,
            "evaluation_results_valid": True,
            "relationships_valid": True
        }
        
        # Validate round
        try:
            round_data = submission.round
            if not round_data.round_id:
                validation_results["round_valid"] = False
            if not round_data.validator_info:
                validation_results["round_valid"] = False
            if not round_data.miners:
                validation_results["round_valid"] = False
        except Exception as e:
            print(f"   ❌ Round validation error: {e}")
            validation_results["round_valid"] = False
        
        # Validate agent runs
        try:
            for agent_run in submission.agent_evaluation_runs:
                if not agent_run.agent_run_id:
                    validation_results["agent_runs_valid"] = False
                if not agent_run.round_id == round_data.round_id:
                    validation_results["agent_runs_valid"] = False
                if not agent_run.validator_uid == round_data.validator_info.uid:
                    validation_results["agent_runs_valid"] = False
        except Exception as e:
            print(f"   ❌ Agent runs validation error: {e}")
            validation_results["agent_runs_valid"] = False
        
        # Validate tasks
        try:
            for task in submission.tasks:
                if not task.task_id:
                    validation_results["tasks_valid"] = False
                if not task.round_id == round_data.round_id:
                    validation_results["tasks_valid"] = False
                if not task.agent_run_id:
                    validation_results["tasks_valid"] = False
        except Exception as e:
            print(f"   ❌ Tasks validation error: {e}")
            validation_results["tasks_valid"] = False
        
        # Validate task solutions
        try:
            for task_solution in submission.task_solutions:
                if not task_solution.solution_id:
                    validation_results["task_solutions_valid"] = False
                if not task_solution.task_id:
                    validation_results["task_solutions_valid"] = False
                if not task_solution.round_id == round_data.round_id:
                    validation_results["task_solutions_valid"] = False
                if not task_solution.agent_run_id:
                    validation_results["task_solutions_valid"] = False
                if not task_solution.validator_uid == round_data.validator_info.uid:
                    validation_results["task_solutions_valid"] = False
        except Exception as e:
            print(f"   ❌ Task solutions validation error: {e}")
            validation_results["task_solutions_valid"] = False
        
        # Validate evaluation results
        try:
            for eval_result in submission.evaluation_results:
                if not eval_result.evaluation_id:
                    validation_results["evaluation_results_valid"] = False
                if not eval_result.task_id:
                    validation_results["evaluation_results_valid"] = False
                if not eval_result.task_solution_id:
                    validation_results["evaluation_results_valid"] = False
                if not eval_result.round_id == round_data.round_id:
                    validation_results["evaluation_results_valid"] = False
                if not eval_result.agent_run_id:
                    validation_results["evaluation_results_valid"] = False
                if not eval_result.validator_uid == round_data.validator_info.uid:
                    validation_results["evaluation_results_valid"] = False
        except Exception as e:
            print(f"   ❌ Evaluation results validation error: {e}")
            validation_results["evaluation_results_valid"] = False
        
        # Validate relationships using model methods
        try:
            # Test agent run validation
            for agent_run in submission.agent_evaluation_runs:
                agent_tasks = [task for task in submission.tasks if task.agent_run_id == agent_run.agent_run_id]
                if not agent_run.validate_task_relationships(agent_tasks):
                    validation_results["relationships_valid"] = False
                    print(f"   ❌ Agent run {agent_run.agent_run_id} relationship validation failed")
            
            # Test task solution validation
            for task_solution in submission.task_solutions:
                # Find corresponding task and agent run
                task = next((t for t in submission.tasks if t.task_id == task_solution.task_id), None)
                agent_run = next((ar for ar in submission.agent_evaluation_runs if ar.agent_run_id == task_solution.agent_run_id), None)
                
                if task and agent_run:
                    if not task_solution.validate_relationships(agent_run, task):
                        validation_results["relationships_valid"] = False
                        print(f"   ❌ Task solution {task_solution.solution_id} relationship validation failed")
            
            # Test evaluation result validation
            for eval_result in submission.evaluation_results:
                # Find corresponding task, task solution, and agent run
                task = next((t for t in submission.tasks if t.task_id == eval_result.task_id), None)
                task_solution = next((ts for ts in submission.task_solutions if ts.solution_id == eval_result.task_solution_id), None)
                agent_run = next((ar for ar in submission.agent_evaluation_runs if ar.agent_run_id == eval_result.agent_run_id), None)
                
                if task and task_solution and agent_run:
                    if not eval_result.validate_relationships(agent_run, task, task_solution):
                        validation_results["relationships_valid"] = False
                        print(f"   ❌ Evaluation result {eval_result.evaluation_id} relationship validation failed")
                        
        except Exception as e:
            print(f"   ❌ Relationship validation error: {e}")
            validation_results["relationships_valid"] = False
        
        # Summary
        all_valid = all(validation_results.values())
        if all_valid:
            print(f"   ✅ All validations passed!")
        else:
            print(f"   ❌ Some validations failed:")
            for key, value in validation_results.items():
                if not value:
                    print(f"      - {key}: FAILED")
        
        return validation_results
    
    def test_json_serialization(self, submission: RoundSubmissionRequest) -> bool:
        """Test JSON serialization of the submission."""
        print(f"🔄 Testing JSON serialization...")
        
        try:
            # Convert to dict
            data_dict = submission.model_dump()
            
            # Serialize to JSON
            json_str = json.dumps(data_dict, default=str)
            
            # Deserialize from JSON
            data_restored = json.loads(json_str)
            
            print(f"   ✅ JSON serialization successful!")
            print(f"   JSON size: {len(json_str)} characters")
            
            return True
            
        except Exception as e:
            print(f"   ❌ JSON serialization error: {e}")
            return False
    
    async def run_validation_tests(self):
        """Run comprehensive validation tests."""
        print("🧪 Autoppia Validator Pipeline - Data Validation Test")
        print("=" * 60)
        
        # Generate test data
        print("\n📊 Generating test data...")
        submissions = await self.generator.generate_all_data(num_rounds=2)
        
        if not submissions:
            print("❌ No test data generated!")
            return
        
        test_results = {
            "structure_validation": [],
            "json_serialization": [],
            "overall_success": True
        }
        
        # Test each submission
        for i, submission in enumerate(submissions):
            print(f"\n📝 Testing submission {i+1}/{len(submissions)}")
            print(f"   Round ID: {submission.round.round_id}")
            
            # Test structure validation
            structure_result = self.validate_submission_structure(submission)
            test_results["structure_validation"].append({
                "round_id": submission.round.round_id,
                "result": structure_result
            })
            
            # Test JSON serialization
            json_result = self.test_json_serialization(submission)
            test_results["json_serialization"].append({
                "round_id": submission.round.round_id,
                "result": json_result
            })
            
            # Update overall success
            if not all(structure_result.values()) or not json_result:
                test_results["overall_success"] = False
        
        # Summary
        print("\n📊 Validation Summary")
        print("=" * 40)
        
        structure_success = sum(1 for test in test_results["structure_validation"] if all(test["result"].values()))
        structure_total = len(test_results["structure_validation"])
        print(f"Structure Validation: {structure_success}/{structure_total} successful")
        
        json_success = sum(1 for test in test_results["json_serialization"] if test["result"])
        json_total = len(test_results["json_serialization"])
        print(f"JSON Serialization: {json_success}/{json_total} successful")
        
        if test_results["overall_success"]:
            print("🎉 All validation tests passed!")
            print("✅ Data structure is ready for API submission!")
        else:
            print("⚠️  Some validation tests failed. Check the output above for details.")
        
        return test_results


async def main():
    """Main function to run validation tests."""
    # Set environment variable for mock mode
    os.environ["USE_MOCK_DB"] = "true"
    
    tester = DataValidationTester()
    
    try:
        results = await tester.run_validation_tests()
        return results
        
    except Exception as e:
        print(f"❌ Error running validation tests: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
