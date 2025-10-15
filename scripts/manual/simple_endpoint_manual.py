#!/usr/bin/env python3
"""
Simple endpoint test that generates data and tests the POST endpoint.
"""

import os
import sys
import asyncio
import json
from pathlib import Path

# Add the app directory to the path
sys.path.append(str(Path(__file__).parent.parent.parent))

# Import the generator directly
sys.path.append(str(Path(__file__).parent.parent / "data_generation"))
from generate_test_data_new import NewTestDataGenerator
from app.models.schemas import RoundSubmissionRequest


async def test_post_endpoint():
    """Test the POST endpoint with generated data."""
    print("🧪 Simple Endpoint Test")
    print("=" * 40)
    
    # Set environment variable for mock mode
    os.environ["USE_MOCK_DB"] = "true"
    
    generator = NewTestDataGenerator()
    
    # Generate test data
    print("📊 Generating test data...")
    submissions = await generator.generate_all_data(num_rounds=1)
    
    if not submissions:
        print("❌ No test data generated!")
        return
    
    submission = submissions[0]
    print(f"✅ Generated submission for round: {submission.round.validator_round_id}")
    print(f"   Validator UID: {submission.round.validator_info.uid}")
    print(f"   Miners: {len(submission.round.miners)}")
    print(f"   Tasks: {len(submission.tasks)}")
    print(f"   Agent Runs: {len(submission.agent_evaluation_runs)}")
    print(f"   Task Solutions: {len(submission.task_solutions)}")
    print(f"   Evaluation Results: {len(submission.evaluation_results)}")
    
    # Test JSON serialization
    print(f"\n🔄 Testing JSON serialization...")
    try:
        data_dict = submission.model_dump()
        json_str = json.dumps(data_dict, default=str)
        print(f"   ✅ JSON serialization successful!")
        print(f"   JSON size: {len(json_str)} characters")
        
        # Test deserialization
        data_restored = json.loads(json_str)
        print(f"   ✅ JSON deserialization successful!")
        
    except Exception as e:
        print(f"   ❌ JSON serialization error: {e}")
        return
    
    # Test model validation (basic)
    print(f"\n🔍 Testing basic model validation...")
    try:
        # Test that we can create a new submission from the data
        new_submission = RoundSubmissionRequest(**data_dict)
        print(f"   ✅ Model validation successful!")
        print(f"   Round ID: {new_submission.round.validator_round_id}")
        print(f"   Validator UID: {new_submission.round.validator_info.uid}")
        
    except Exception as e:
        print(f"   ❌ Model validation error: {e}")
        return
    
    print(f"\n🎉 All tests passed!")
    print(f"✅ Data is ready for API submission!")
    print(f"📝 You can now test the POST /v1/rounds/submit endpoint with this data")
    
    # Save example data to file
    example_file = Path(__file__).parent / "example_submission.json"
    with open(example_file, 'w') as f:
        json.dump(data_dict, f, indent=2, default=str)
    print(f"💾 Example data saved to: {example_file}")
    
    return submission


async def main():
    """Main function."""
    try:
        submission = await test_post_endpoint()
        return submission
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    asyncio.run(main())
