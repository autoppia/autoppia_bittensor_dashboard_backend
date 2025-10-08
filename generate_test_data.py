#!/usr/bin/env python3
"""
Generate realistic test data for the validator pipeline API.
This script creates mock data that simulates real validator rounds and evaluations.
"""

import os
import sys
import asyncio
import time
import random
from typing import List, Dict, Any
from pathlib import Path

# Add the app directory to the path
sys.path.append(str(Path(__file__).parent))

from app.db.mock_mongo import get_mock_db
from app.models.schemas import (
    Round, ValidatorInfo, MinerInfo, Task, AgentEvaluationRun, TaskExecution,
    RoundStatus, TaskStatus, EvaluationStatus
)


class TestDataGenerator:
    """Generate realistic test data for the validator pipeline."""
    
    def __init__(self):
        self.db = get_mock_db()
        self.validators = [
            ValidatorInfo(validator_uid=123, validator_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"),
            ValidatorInfo(validator_uid=456, validator_hotkey="5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"),
            ValidatorInfo(validator_uid=789, validator_hotkey="5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy")
        ]
        
        self.miners = [
            MinerInfo(miner_uid=1, miner_hotkey="5HGjWAeFDfFCWPsjFQdVV2Msvz2XtMktvgocEYSj2FQjYq9c"),
            MinerInfo(miner_uid=2, miner_hotkey="5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"),
            MinerInfo(miner_uid=3, miner_hotkey="5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"),
            MinerInfo(miner_uid=4, miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"),
            MinerInfo(miner_uid=5, miner_hotkey="5HGjWAeFDfFCWPsjFQdVV2Msvz2XtMktvgocEYSj2FQjYq9c"),
            MinerInfo(miner_uid=6, miner_hotkey="5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty")
        ]
        
        self.websites = [
            "amazon.com", "google.com", "facebook.com", "twitter.com", "linkedin.com",
            "github.com", "stackoverflow.com", "reddit.com", "youtube.com", "netflix.com"
        ]
        
        self.web_projects = ["ecommerce", "blog", "social_media", "search_engine", "streaming"]
        self.use_cases = ["search", "navigate", "purchase", "login", "upload", "download", "share"]
        
        self.task_prompts = [
            "Search for 'laptop' and add the first result to cart",
            "Navigate to the login page and sign in with test credentials",
            "Find the contact information and send a message",
            "Browse the latest posts and like the first 3 posts",
            "Search for 'python tutorial' and open the first video",
            "Navigate to settings and change the theme to dark mode",
            "Find a product under $100 and add it to wishlist",
            "Create a new post with the title 'Test Post'",
            "Search for 'machine learning' and bookmark the first article",
            "Navigate to profile and update the bio"
        ]
    
    def generate_task(self, round_id: str, task_index: int) -> Task:
        """Generate a realistic task."""
        return Task(
            task_id=f"{round_id}_task_{task_index:04d}",
            prompt=random.choice(self.task_prompts),
            website=random.choice(self.websites),
            web_project=random.choice(self.web_projects),
            use_case=random.choice(self.use_cases),
            expected_actions=[
                {"type": "click", "selector": "button.search"},
                {"type": "type", "text": "test query", "selector": "input[name='q']"},
                {"type": "wait", "duration": 2.0}
            ],
            max_execution_time=30.0,
            difficulty=random.uniform(0.3, 0.9),
            metadata={
                "generated_at": time.time(),
                "generator": "test_data",
                "complexity": random.choice(["low", "medium", "high"])
            }
        )
    
    def generate_round(self, round_id: str, validator: ValidatorInfo, status: RoundStatus = RoundStatus.completed) -> Round:
        """Generate a realistic round."""
        start_time = time.time() - random.uniform(3600, 86400)  # 1 hour to 1 day ago
        end_time = start_time + random.uniform(300, 1800)  # 5 to 30 minutes duration
        
        # Select random miners for this round
        selected_miners = random.sample(self.miners, random.randint(3, 6))
        
        # Generate tasks
        n_tasks = random.randint(5, 15)
        tasks = [self.generate_task(round_id, i) for i in range(n_tasks)]
        
        # Generate winners if completed
        winners = None
        weights = None
        if status == RoundStatus.completed:
            winners = []
            weights = {}
            for i, miner in enumerate(selected_miners[:3]):  # Top 3 winners
                rank = i + 1
                score = 0.9 - (i * 0.1) + random.uniform(-0.05, 0.05)
                reward = score * 10
                weight = [0.8, 0.15, 0.05][i] if i < 3 else 0.0
                
                winners.append({
                    "miner_uid": miner.miner_uid,
                    "rank": rank,
                    "score": round(score, 3),
                    "reward": round(reward, 3)
                })
                weights[miner.miner_uid] = weight
        
        return Round(
            round_id=round_id,
            validator_info=validator,
            status=status,
            start_block=random.randint(1000, 10000),
            start_epoch=random.randint(50, 200),
            end_block=random.randint(1000, 10000) if status == RoundStatus.completed else None,
            end_epoch=random.randint(50, 200) if status == RoundStatus.completed else None,
            started_at=start_time,
            ended_at=end_time if status == RoundStatus.completed else None,
            elapsed_sec=end_time - start_time if status == RoundStatus.completed else None,
            max_epochs=20,
            max_blocks=360,
            n_tasks=n_tasks,
            n_miners=len(selected_miners),
            n_winners=3,
            miners=selected_miners,
            tasks=tasks,
            agent_evaluation_runs=[f"{round_id}_{miner.miner_uid}" for miner in selected_miners],
            winners=winners,
            weights=weights,
            metadata={
                "generated_at": time.time(),
                "generator": "test_data",
                "network": "testnet",
                "version": "1.0"
            }
        )
    
    def generate_agent_evaluation_run(self, round_id: str, validator: ValidatorInfo, miner: MinerInfo, round_data: Round) -> AgentEvaluationRun:
        """Generate an agent evaluation run."""
        agent_run_id = f"{round_id}_{miner.miner_uid}"
        
        # Calculate performance metrics
        n_tasks = len(round_data.tasks)
        n_completed = random.randint(int(n_tasks * 0.7), n_tasks)  # 70-100% completion
        n_failed = n_tasks - n_completed
        
        # Generate realistic scores
        base_score = random.uniform(0.6, 0.95)
        avg_eval_score = base_score + random.uniform(-0.1, 0.1)
        avg_execution_time = random.uniform(3.0, 15.0)
        total_reward = avg_eval_score * n_completed * 2.0
        
        # Determine rank based on performance
        rank = None
        weight = None
        if round_data.status == RoundStatus.completed and round_data.winners:
            for winner in round_data.winners:
                if winner["miner_uid"] == miner.miner_uid:
                    rank = winner["rank"]
                    weight = round_data.weights.get(miner.miner_uid, 0.0)
                    break
        
        return AgentEvaluationRun(
            agent_run_id=agent_run_id,
            round_id=round_id,
            validator_info=validator,
            miner_info=miner,
            started_at=round_data.started_at,
            ended_at=round_data.ended_at,
            elapsed_sec=round_data.elapsed_sec,
            task_ids=[task.task_id for task in round_data.tasks],
            n_tasks_total=n_tasks,
            n_tasks_completed=n_completed,
            n_tasks_failed=n_failed,
            avg_eval_score=round(avg_eval_score, 3),
            avg_execution_time=round(avg_execution_time, 2),
            total_reward=round(total_reward, 3),
            rank=rank,
            weight=weight,
            status=EvaluationStatus.completed if round_data.status == RoundStatus.completed else EvaluationStatus.pending,
            metadata={
                "generated_at": time.time(),
                "generator": "test_data",
                "performance_tier": random.choice(["excellent", "good", "average", "poor"])
            }
        )
    
    def generate_task_execution(self, task: Task, agent_run_id: str, round_id: str, validator: ValidatorInfo, miner: MinerInfo) -> TaskExecution:
        """Generate a task execution."""
        # Generate realistic execution data
        execution_time = random.uniform(2.0, 20.0)
        eval_score = random.uniform(0.5, 1.0)
        time_score = max(0.0, 1.0 - (execution_time - 5.0) / 15.0)  # Penalty for slow execution
        total_score = (eval_score + time_score) / 2
        reward = total_score * 2.0
        
        # Generate miner response
        miner_response = {
            "actions": [
                {"type": "click", "selector": "button.search", "timestamp": time.time()},
                {"type": "type", "text": "test query", "selector": "input[name='q']", "timestamp": time.time() + 1},
                {"type": "wait", "duration": 2.0, "timestamp": time.time() + 2}
            ],
            "execution_time": execution_time,
            "success": random.choice([True, True, True, False]),  # 75% success rate
            "error_message": None if random.random() > 0.25 else "Element not found"
        }
        
        return TaskExecution(
            task_id=task.task_id,
            agent_run_id=agent_run_id,
            round_id=round_id,
            validator_info=validator,
            miner_info=miner,
            task=task,
            sent_at=time.time() - execution_time - 1,
            started_at=time.time() - execution_time,
            completed_at=time.time(),
            execution_time=execution_time,
            miner_response=miner_response,
            web_actions=miner_response["actions"],
            eval_score=round(eval_score, 3),
            time_score=round(time_score, 3),
            total_score=round(total_score, 3),
            reward=round(reward, 3),
            evaluation_result={
                "correctness": eval_score,
                "efficiency": time_score,
                "evaluated_at": time.time(),
                "criteria_met": random.choice([True, False])
            },
            test_results={
                "tests_passed": random.randint(3, 8),
                "tests_total": 8,
                "coverage": random.uniform(0.7, 1.0)
            },
            status=TaskStatus.completed,
            metadata={
                "generated_at": time.time(),
                "generator": "test_data",
                "browser": random.choice(["chrome", "firefox", "safari"]),
                "os": random.choice(["linux", "windows", "macos"])
            }
        )
    
    async def generate_all_data(self, num_rounds: int = 10):
        """Generate comprehensive test data."""
        print(f"🚀 Generating test data for {num_rounds} rounds...")
        
        all_rounds = []
        all_agent_runs = []
        all_task_executions = []
        all_tasks = []
        
        for i in range(num_rounds):
            # Select random validator
            validator = random.choice(self.validators)
            round_id = f"round_{int(time.time())}_{i:03d}"
            
            # Generate round with random status
            status = random.choice([
                RoundStatus.completed,
                RoundStatus.completed,
                RoundStatus.completed,  # 75% completed
                RoundStatus.evaluation,
                RoundStatus.scoring,
                RoundStatus.task_distribution
            ])
            
            round_data = self.generate_round(round_id, validator, status)
            all_rounds.append(round_data)
            
            # Generate tasks
            for task in round_data.tasks:
                all_tasks.append(task)
            
            # Generate agent evaluation runs
            for miner in round_data.miners:
                agent_run = self.generate_agent_evaluation_run(round_id, validator, miner, round_data)
                all_agent_runs.append(agent_run)
                
                # Generate task executions
                for task in round_data.tasks:
                    task_execution = self.generate_task_execution(
                        task, agent_run.agent_run_id, round_id, validator, miner
                    )
                    all_task_executions.append(task_execution)
            
            print(f"✅ Generated round {i+1}/{num_rounds}: {round_id} ({status})")
        
        # Store all data in mock database
        print("💾 Storing data in mock database...")
        
        # Store rounds
        for round_data in all_rounds:
            await self.db.rounds.insert_one(round_data.model_dump())
        
        # Store tasks
        for task in all_tasks:
            await self.db.tasks.insert_one(task.model_dump())
        
        # Store agent evaluation runs
        for agent_run in all_agent_runs:
            await self.db.agent_evaluation_runs.insert_one(agent_run.model_dump())
        
        # Store task executions
        for task_execution in all_task_executions:
            await self.db.task_executions.insert_one(task_execution.model_dump())
        
        print(f"🎉 Test data generation completed!")
        print(f"📊 Generated:")
        print(f"   - {len(all_rounds)} rounds")
        print(f"   - {len(all_tasks)} tasks")
        print(f"   - {len(all_agent_runs)} agent evaluation runs")
        print(f"   - {len(all_task_executions)} task executions")
        
        return {
            "rounds": len(all_rounds),
            "tasks": len(all_tasks),
            "agent_runs": len(all_agent_runs),
            "task_executions": len(all_task_executions)
        }


async def main():
    """Main function to generate test data."""
    print("🧪 Autoppia Validator Pipeline - Test Data Generator")
    print("=" * 60)
    
    # Set environment variable for mock mode
    os.environ["USE_MOCK_DB"] = "true"
    
    generator = TestDataGenerator()
    
    try:
        # Generate test data
        stats = await generator.generate_all_data(num_rounds=15)
        
        print("\n📈 Data Statistics:")
        for key, value in stats.items():
            print(f"   {key}: {value}")
        
        print(f"\n📁 Mock data stored in: mock_data/")
        print(f"🔗 You can now start the API server and test the endpoints!")
        
    except Exception as e:
        print(f"❌ Error generating test data: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
