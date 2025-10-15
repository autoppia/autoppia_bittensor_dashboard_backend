#!/usr/bin/env python3
"""
Generate realistic test data for the new validator pipeline API design.
This script creates mock data that aligns with the new 5-collection model.
"""

import os
import sys
import asyncio
import time
import random
import uuid
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime

# Add the app directory to the path
sys.path.append(str(Path(__file__).parent.parent.parent))

from app.db.mock_mongo import get_mock_db
from app.models.schemas import (
    Round, ValidatorInfo, MinerInfo, Task, AgentEvaluationRun, 
    TaskSolution, EvaluationResult, Action, TestResult, Feedback,
    RoundSubmissionRequest
)


class NewTestDataGenerator:
    """Generate realistic test data for the new validator pipeline design."""
    
    def __init__(self):
        self.db = get_mock_db()
        self.asset_base = "https://assets.autoppia.com"

        self.validators = [
            ValidatorInfo(uid=123, hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY", stake=1000.0),
            ValidatorInfo(uid=456, hotkey="5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty", stake=2000.0),
            ValidatorInfo(uid=789, hotkey="5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy", stake=1500.0)
        ]
        
        self.miners = [
            MinerInfo(
                uid=1,
                hotkey="5HGjWAeFDfFCWPsjFQdVV2Msvz2XtMktvgocEYSj2FQjYq9c",
                stake=500.0,
                agent_name="Autoppia Prime",
                agent_image=f"{self.asset_base}/agents/autoppia_prime.png",
                github="https://github.com/autoppia/prime"
            ),
            MinerInfo(
                uid=2,
                hotkey="5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
                stake=750.0,
                agent_name="Browser Pilot",
                agent_image=f"{self.asset_base}/agents/browser_pilot.png",
                github="https://github.com/browser/pilot"
            ),
            MinerInfo(
                uid=3,
                hotkey="5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy",
                stake=600.0,
                agent_name="Automation Titan",
                agent_image=f"{self.asset_base}/agents/automation_titan.png",
                github="https://github.com/automation/titan"
            ),
            MinerInfo(
                uid=4,
                hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                stake=800.0,
                agent_name="Web Maestro",
                agent_image=f"{self.asset_base}/agents/web_maestro.png",
                github="https://github.com/maestro/web"
            ),
            MinerInfo(
                uid=5,
                hotkey="5HGjWAeFDfFCWPsjFQdVV2Msvz2XtMktvgocEYSj2FQjYq9c",
                stake=400.0,
                agent_name="Navigator Lite",
                agent_image=f"{self.asset_base}/agents/navigator_lite.png",
                github="https://github.com/navigator/lite"
            ),
            MinerInfo(
                uid=6,
                hotkey="5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
                stake=900.0,
                agent_name="FormRunner",
                agent_image=f"{self.asset_base}/agents/form_runner.png",
                github="https://github.com/form/runner"
            )
        ]

        self.sota_agents = [
            MinerInfo(
                uid=None,
                hotkey=None,
                agent_name="OpenAI GPT-4o",
                agent_image=f"{self.asset_base}/agents/sota_openai_gpt4o.png",
                github="https://github.com/openai/gpt-4o",
                is_sota=True,
                description="OpenAI benchmark agent for multimodal tasks",
                provider="OpenAI"
            ),
            MinerInfo(
                uid=None,
                hotkey=None,
                agent_name="Claude 3.5 Sonnet",
                agent_image=f"{self.asset_base}/agents/sota_claude_35_sonnet.png",
                github="https://github.com/anthropic/claude-3-5-sonnet",
                is_sota=True,
                description="Anthropic benchmark agent for enterprise tasks",
                provider="Anthropic"
            ),
            MinerInfo(
                uid=None,
                hotkey=None,
                agent_name="Browser Use",
                agent_image=f"{self.asset_base}/agents/sota_browser_use.png",
                github="https://github.com/browser-use/browser-use",
                is_sota=True,
                description="Community-driven browser-use reference agent",
                provider="Community"
            )
        ]
        
        self.websites = [
            "amazon.com", "google.com", "facebook.com", "twitter.com", "linkedin.com",
            "github.com", "stackoverflow.com", "reddit.com", "youtube.com", "netflix.com"
        ]
        
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
    
    def generate_action(self, action_type: str = None) -> Action:
        """Generate a realistic web action."""
        if action_type is None:
            action_type = random.choice(["click", "type", "wait", "scroll", "hover"])
        
        if action_type == "click":
            return Action(
                type="click",
                attributes={
                    "selector": random.choice([
                        "button.search", "a.login", "div.product", 
                        "input.submit", "span.menu-item"
                    ]),
                    "timestamp": time.time()
                }
            )
        elif action_type == "type":
            return Action(
                type="type",
                attributes={
                    "text": random.choice(["test query", "username", "password", "search term"]),
                    "selector": random.choice([
                        "input[name='q']", "input[name='username']", 
                        "input[name='password']", "textarea.comment"
                    ]),
                    "timestamp": time.time()
                }
            )
        elif action_type == "wait":
            return Action(
                type="wait",
                attributes={
                    "duration": random.uniform(1.0, 5.0),
                    "timestamp": time.time()
                }
            )
        elif action_type == "scroll":
            return Action(
                type="scroll",
                attributes={
                    "direction": random.choice(["up", "down"]),
                    "amount": random.randint(100, 500),
                    "timestamp": time.time()
                }
            )
        else:  # hover
            return Action(
                type="hover",
                attributes={
                    "selector": random.choice([
                        "div.product", "a.link", "button.menu"
                    ]),
                    "timestamp": time.time()
                }
            )

    def _slugify_agent(self, miner: MinerInfo) -> str:
        """Create a stable identifier for agent run IDs."""
        if miner.uid is not None:
            return str(miner.uid)
        base = miner.agent_name or "benchmark"
        return base.lower().replace(" ", "-").replace("/", "-")

    def _build_miner_profile(self, miner: MinerInfo) -> MinerInfo:
        """Return a copy of miner info to avoid mutating shared instances."""
        image = miner.agent_image or ""
        if image and not image.startswith("http"):
            image = f"{self.asset_base}{image}" if image.startswith("/") else f"{self.asset_base}/{image}"
        return MinerInfo(
            uid=miner.uid,
            hotkey=miner.hotkey,
            coldkey=miner.coldkey,
            agent_name=miner.agent_name,
            agent_image=image,
            github=miner.github,
            is_sota=miner.is_sota,
            description=miner.description,
            provider=miner.provider
        )
    
    def generate_task(self, validator_round_id: str, agent_run_id: str, task_index: int) -> Task:
        """Generate a realistic task."""
        return Task(
            task_id=f"{validator_round_id}_task_{task_index:04d}_{agent_run_id}",
            validator_round_id=validator_round_id,
            agent_run_id=agent_run_id,
            scope=random.choice(["global", "local"]),
            is_web_real=random.choice([True, False]),
            web_project_id=random.choice(["ecommerce", "blog", "social_media", "search_engine"]),
            url=f"https://{random.choice(self.websites)}",
            prompt=random.choice(self.task_prompts),
            html=f"<html><body><h1>Test Page {task_index}</h1></body></html>",
            clean_html=f"<h1>Test Page {task_index}</h1>",
            interactive_elements='{"buttons": ["search", "login"], "inputs": ["query", "username"]}',
            screenshot=f"base64_screenshot_data_{task_index}",
            screenshot_description=f"Screenshot of test page {task_index}",
            specifications={
                "browser": random.choice(["chrome", "firefox", "safari"]),
                "viewport": {"width": 1920, "height": 1080},
                "timeout": 30
            },
            created_at=datetime.now()
        )
    
    def generate_task_solution(self, task: Task, agent_run_id: str, validator_round_id: str, 
                             validator_uid: int, miner: MinerInfo) -> TaskSolution:
        """Generate a realistic task solution."""
        # Generate 3-8 actions
        n_actions = random.randint(3, 8)
        actions = [self.generate_action() for _ in range(n_actions)]
        miner_slug = self._slugify_agent(miner)
        
        return TaskSolution(
            solution_id=str(uuid.uuid4()),
            task_id=task.task_id,
            validator_round_id=validator_round_id,
            agent_run_id=agent_run_id,
            miner_uid=miner.uid,
            validator_uid=validator_uid,
            actions=actions,
            web_agent_id=f"agent_{miner_slug}_{int(time.time())}",
            recording=f"recording_data_{task.task_id}"
        )
    
    def generate_evaluation_result(self, task: Task, task_solution: TaskSolution, 
                                 agent_run_id: str, validator_round_id: str, 
                                 validator_uid: int, miner: MinerInfo) -> EvaluationResult:
        """Generate a realistic evaluation result."""
        # Generate test results
        n_tests = random.randint(5, 10)
        test_results_matrix = []
        passed_tests = 0
        
        for i in range(n_tests):
            test_row = []
            for j in range(random.randint(1, 3)):  # Multiple test runs
                success = random.choice([True, True, True, False])  # 75% success rate
                if success:
                    passed_tests += 1
                test_row.append(TestResult(
                    success=success,
                    extra_data={"test_id": f"test_{i}_{j}", "duration": random.uniform(0.1, 2.0)}
                ))
            test_results_matrix.append(test_row)
        
        # Generate execution history
        execution_history = [
            {"action": "start", "timestamp": time.time() - 10},
            {"action": "navigate", "timestamp": time.time() - 9},
            {"action": "interact", "timestamp": time.time() - 7},
            {"action": "complete", "timestamp": time.time()}
        ]
        
        # Calculate scores
        final_score = (passed_tests / (n_tests * 3)) * 10  # Scale to 0-10
        raw_score = final_score * 0.8  # Raw score is typically lower
        
        # Generate feedback
        feedback = Feedback(
            task_prompt=task.prompt,
            final_score=round(final_score, 2),
            executed_actions=len(task_solution.actions),
            failed_actions=random.randint(0, 2),
            passed_tests=passed_tests,
            failed_tests=(n_tests * 3) - passed_tests,
            total_execution_time=random.uniform(5.0, 30.0),
            time_penalty=random.uniform(0.0, 2.0),
            critical_test_penalty=random.randint(0, 3),
            test_results=[tr for row in test_results_matrix for tr in row],
            execution_history=execution_history
        )
        
        return EvaluationResult(
            evaluation_id=str(uuid.uuid4()),
            task_id=task.task_id,
            task_solution_id=task_solution.solution_id,
            validator_round_id=validator_round_id,
            agent_run_id=agent_run_id,
            miner_uid=miner.uid,
            validator_uid=validator_uid,
            final_score=round(final_score, 2),
            test_results_matrix=test_results_matrix,
            execution_history=execution_history,
            feedback=feedback,
            web_agent_id=task_solution.web_agent_id,
            raw_score=round(raw_score, 2),
            evaluation_time=random.uniform(2.0, 10.0)
        )
    
    def generate_agent_evaluation_run(
        self,
        validator_round_id: str,
        validator_uid: int,
        miner: MinerInfo,
        tasks: List[Task],
    ) -> AgentEvaluationRun:
        """Generate an agent evaluation run."""
        miner_slug = self._slugify_agent(miner)
        agent_run_id = f"{validator_round_id}_{miner_slug}"
        
        # Calculate performance metrics
        n_tasks = len(tasks)
        n_completed = random.randint(int(n_tasks * 0.7), n_tasks)  # 70-100% completion
        n_failed = n_tasks - n_completed
        
        # Generate realistic scores
        base_score = random.uniform(0.6, 0.95)
        avg_eval_score = base_score + random.uniform(-0.1, 0.1)
        avg_execution_time = random.uniform(3.0, 15.0)
        total_reward = avg_eval_score * n_completed * 2.0
        
        return AgentEvaluationRun(
            agent_run_id=agent_run_id,
            validator_round_id=validator_round_id,
            validator_uid=validator_uid,
            miner_uid=miner.uid,
            miner_info=self._build_miner_profile(miner),
            is_sota=miner.is_sota,
            started_at=time.time() - random.uniform(300, 1800),  # 5-30 minutes ago
            ended_at=time.time(),
            elapsed_sec=random.uniform(300, 1800),
            task_ids=[task.task_id for task in tasks],
            n_tasks_total=n_tasks,
            n_tasks_completed=n_completed,
            n_tasks_failed=n_failed,
            avg_eval_score=round(avg_eval_score, 3),
            avg_execution_time=round(avg_execution_time, 2),
            total_reward=round(total_reward, 3),
            rank=random.randint(1, 6) if random.random() > 0.2 else None,
            weight=random.uniform(0.1, 0.9) if random.random() > 0.2 else None,
            status=random.choice(["completed", "pending", "failed"]),
            metadata={
                "generated_at": time.time(),
                "generator": "new_test_data",
                "performance_tier": random.choice(["excellent", "good", "average", "poor"])
            }
        )
    
    def generate_round(self, validator_round_id: str, validator: ValidatorInfo) -> Round:
        """Generate a realistic round."""
        start_time = time.time() - random.uniform(3600, 86400)  # 1 hour to 1 day ago
        end_time = start_time + random.uniform(300, 1800)  # 5 to 30 minutes duration
        
        # Select random miners for this round
        selected_miners = [self._build_miner_profile(m) for m in random.sample(self.miners, random.randint(3, 6))]
        sota_agents = [self._build_miner_profile(agent) for agent in self.sota_agents]
        
        # Generate winners
        winners = []
        for i, miner in enumerate(selected_miners[:3]):  # Top 3 winners
            rank = i + 1
            score = 0.9 - (i * 0.1) + random.uniform(-0.05, 0.05)
            reward = score * 10
            
            winners.append({
                "miner_uid": miner.uid,
                "rank": rank,
                "score": round(score, 3),
                "reward": round(reward, 3)
            })
        
        return Round(
            validator_round_id=validator_round_id,
            validators=[validator],
            miners=selected_miners,
            sota_agents=sota_agents,
            start_block=random.randint(1000, 10000),
            start_epoch=random.randint(50, 200),
            end_block=random.randint(1000, 10000),
            end_epoch=random.randint(50, 200),
            started_at=start_time,
            ended_at=end_time,
            elapsed_sec=end_time - start_time,
            max_epochs=20,
            max_blocks=360,
            n_tasks=random.randint(5, 15),
            n_miners=len(selected_miners),
            n_winners=3,
            winners=winners,
            metadata={
                "generated_at": time.time(),
                "generator": "new_test_data",
                "network": "testnet",
                "version": "2.0"
            }
        )
    
    async def generate_round_submission(self, validator_round_id: str) -> RoundSubmissionRequest:
        """Generate a complete round submission with all related data."""
        # Select random validator
        validator = random.choice(self.validators)
        
        # Generate round
        round_data = self.generate_round(validator_round_id, validator)
        
        # Generate shared tasks for the round (5-15 tasks)
        n_tasks = random.randint(5, 15)
        tasks = []
        task_solutions = []
        evaluation_results = []
        agent_evaluation_runs = []
        participants: List[MinerInfo] = round_data.miners + round_data.sota_agents
        
        # Generate tasks first (each task will be worked on by all miners)
        for i in range(n_tasks):
            # Create a task for each miner
            for miner in participants:
                slug = self._slugify_agent(miner)
                agent_run_id = f"{validator_round_id}_{slug}"
                task = self.generate_task(validator_round_id, agent_run_id, i)
                tasks.append(task)
        
        # Generate agent evaluation runs with the correct task count
        for miner in participants:
            # Get tasks for this miner
            slug = self._slugify_agent(miner)
            miner_tasks = [task for task in tasks if task.agent_run_id == f"{validator_round_id}_{slug}"]
            
            # Generate agent evaluation run with the correct tasks
            agent_run = self.generate_agent_evaluation_run(
                validator_round_id, validator.uid, miner, miner_tasks
            )
            agent_evaluation_runs.append(agent_run)
        
        # Generate task solutions and evaluation results
        for task in tasks:
            # Find the corresponding agent run
            agent_run = next(ar for ar in agent_evaluation_runs if ar.agent_run_id == task.agent_run_id)
            # Resolve miner profile for agent run
            run_miner_info = agent_run.miner_info if agent_run.miner_info is not None else MinerInfo(
                uid=agent_run.miner_uid,
                hotkey=None,
                agent_name="Benchmark Agent" if agent_run.is_sota else f"Agent {agent_run.miner_uid}",
                is_sota=agent_run.is_sota
            )
            if agent_run.is_sota:
                miner_profile = next(
                    (m for m in round_data.sota_agents if self._slugify_agent(m) == self._slugify_agent(run_miner_info)),
                    self._build_miner_profile(run_miner_info),
                )
            else:
                miner_profile = next(
                    (m for m in round_data.miners if m.uid == agent_run.miner_uid),
                    self._build_miner_profile(run_miner_info),
                )
            
            # Generate task solution
            task_solution = self.generate_task_solution(
                task, agent_run.agent_run_id, validator_round_id, 
                validator.uid, miner_profile
            )
            task_solutions.append(task_solution)
            
            # Generate evaluation result
            evaluation_result = self.generate_evaluation_result(
                task, task_solution, agent_run.agent_run_id, 
                validator_round_id, validator.uid, miner_profile
            )
            evaluation_results.append(evaluation_result)
        
        return RoundSubmissionRequest(
            round=round_data,
            agent_evaluation_runs=agent_evaluation_runs,
            tasks=tasks,
            task_solutions=task_solutions,
            evaluation_results=evaluation_results
        )
    
    async def generate_all_data(self, num_rounds: int = 5):
        """Generate comprehensive test data."""
        print(f"🚀 Generating test data for {num_rounds} rounds...")
        
        all_submissions = []
        
        for i in range(num_rounds):
            validator_round_id = f"round_{int(time.time())}_{i:03d}"
            submission = await self.generate_round_submission(validator_round_id)
            all_submissions.append(submission)
            
            print(f"✅ Generated round {i+1}/{num_rounds}: {validator_round_id}")
            print(f"   - {len(submission.tasks)} tasks")
            print(f"   - {len(submission.agent_evaluation_runs)} agent runs")
            print(f"   - {len(submission.task_solutions)} task solutions")
            print(f"   - {len(submission.evaluation_results)} evaluation results")
        
        print(f"🎉 Test data generation completed!")
        print(f"📊 Generated {num_rounds} complete round submissions")
        
        return all_submissions


async def main():
    """Main function to generate test data."""
    print("🧪 Autoppia Validator Pipeline - New Test Data Generator")
    print("=" * 60)
    
    # Set environment variable for mock mode
    os.environ["USE_MOCK_DB"] = "true"
    
    generator = NewTestDataGenerator()
    
    try:
        # Generate test data
        submissions = await generator.generate_all_data(num_rounds=3)
        
        print(f"\n📁 Generated {len(submissions)} round submissions")
        print(f"🔗 You can now test the POST endpoints with this data!")
        
        # Save first submission as example
        if submissions:
            example = submissions[0]
            print(f"\n📝 Example submission structure:")
            print(f"   Round ID: {example.round.validator_round_id}")
            print(f"   Validator UID: {example.round.validator_info.uid}")
            print(f"   Miners: {len(example.round.miners)}")
            print(f"   Tasks: {len(example.tasks)}")
            print(f"   Agent Runs: {len(example.agent_evaluation_runs)}")
            print(f"   Task Solutions: {len(example.task_solutions)}")
            print(f"   Evaluation Results: {len(example.evaluation_results)}")
        
        return submissions
        
    except Exception as e:
        print(f"❌ Error generating test data: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
