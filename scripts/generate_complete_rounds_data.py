#!/usr/bin/env python3
"""
Generate complete rounds 1-20 data including all related collections.
"""
import json
import random
import time
import uuid
from datetime import datetime, timedelta

def generate_complete_rounds_data():
    """Generate complete data for rounds 1-20."""
    
    # Generate rounds
    rounds = []
    agent_evaluation_runs = []
    tasks = []
    task_solutions = []
    evaluation_results = []
    
    # Base timestamp for round 1 (20 days ago)
    base_timestamp = time.time() - (20 * 24 * 60 * 60)  # 20 days ago
    
    for round_num in range(1, 21):
        # Calculate timestamps for this round
        round_start = base_timestamp + (round_num - 1) * (24 * 60 * 60)  # 1 day per round
        round_end = round_start + random.randint(1800, 3600)  # 30-60 minutes duration
        
        # Generate realistic data
        n_tasks = random.randint(8, 15)
        n_miners = random.randint(3, 8)
        n_winners = random.randint(2, min(n_miners, 5))
        
        # Generate winners with scores
        winners = []
        for i in range(n_winners):
            score = round(random.uniform(0.6, 0.95), 3)
            winners.append({
                "miner_uid": random.randint(1, 10),
                "score": score,
                "rank": i + 1,
                "reward": round(score * 100, 2)
            })
        
        # Sort winners by score (descending)
        winners.sort(key=lambda x: x["score"], reverse=True)
        
        # Generate miners list
        miners = []
        for i in range(n_miners):
            miners.append({
                "uid": random.randint(1, 10),
                "hotkey": f"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY{i:02d}",
                "coldkey": None,
                "agent_name": f"Agent {i+1}",
                "agent_image": f"/agents/agent_{i+1}.png",
                "github": f"https://github.com/agent{i+1}/autoppia-agent"
            })
        
        # Create round data with 3 validators
        validators = [
            {
                "uid": 123,
                "hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                "coldkey": None,
                "stake": 1000.0,
                "vtrust": round(random.uniform(0.8, 1.0), 2),
                "name": "Autoppia",
                "version": "7.0.0"
            },
            {
                "uid": 124,
                "hotkey": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
                "coldkey": None,
                "stake": 1500.0,
                "vtrust": round(random.uniform(0.7, 0.9), 2),
                "name": "Tao5",
                "version": "6.2.1"
            },
            {
                "uid": 125,
                "hotkey": "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy",
                "coldkey": None,
                "stake": 800.0,
                "vtrust": round(random.uniform(0.6, 0.8), 2),
                "name": "RoundTable21",
                "version": "5.8.3"
            }
        ]
        
        # Select a random validator for this round
        validator = random.choice(validators)
        
        round_data = {
            "round_id": f"round_{round_num:03d}",
            "validator_info": validator,
            "start_block": 1000 + (round_num * 100),
            "start_epoch": round_num,
            "end_block": 1000 + (round_num * 100) + random.randint(50, 200),
            "end_epoch": round_num + 1,
            "started_at": round_start,
            "ended_at": round_end,
            "elapsed_sec": round_end - round_start,
            "max_epochs": 20,
            "max_blocks": 360,
            "n_tasks": n_tasks,
            "n_miners": n_miners,
            "n_winners": n_winners,
            "miners": miners,
            "winners": winners,
            "winner_scores": [w["score"] for w in winners],
            "weights": {miner["uid"]: round(random.uniform(0.1, 1.0), 3) for miner in miners}
        }
        rounds.append(round_data)
        
        # Generate agent evaluation runs for this round
        for agent_idx in range(n_miners):
            agent_run_id = f"round_{round_num:03d}_{agent_idx + 1}"
            agent_run = {
                "agent_run_id": agent_run_id,
                "round_id": f"round_{round_num:03d}",
                "validator_uid": validator["uid"],  # Use the selected validator
                "miner_uid": miners[agent_idx]["uid"],  # Required field
                "version": "1.0",
                "started_at": round_start + random.randint(0, 300),
                "ended_at": round_end - random.randint(0, 300),
                "elapsed_sec": round_end - round_start - random.randint(0, 300),
                "avg_eval_score": round(random.uniform(0.5, 0.9), 3),
                "avg_execution_time": round(random.uniform(30, 120), 2),
                "avg_reward": round(random.uniform(50, 100), 2),
                "rank": random.randint(1, n_miners),
                "weight": round(random.uniform(0.1, 1.0), 3)
            }
            agent_evaluation_runs.append(agent_run)
            
            # Generate tasks for this agent run
            for task_idx in range(n_tasks):
                task_id = f"task_{round_num:03d}_{agent_idx + 1}_{task_idx + 1}"
                task = {
                    "task_id": task_id,
                    "agent_run_id": agent_run_id,
                    "round_id": f"round_{round_num:03d}",
                    "url": f"https://example{random.randint(1, 10)}.com",  # Required field
                    "prompt": f"Task {task_idx + 1} for round {round_num}: {random.choice(['Login to website', 'Fill out form', 'Navigate to page', 'Extract data'])}",  # Required field
                    "task_type": random.choice(["login", "navigation", "form_filling", "data_extraction"]),
                    "created_at": round_start + random.randint(0, 300),
                    "status": "completed" if random.random() > 0.1 else "failed",
                    "difficulty": random.choice(["easy", "medium", "hard"]),
                    "expected_actions": random.randint(3, 8),
                    "time_limit": random.randint(60, 300)
                }
                tasks.append(task)
                
                # Generate task solution
                task_solution = {
                    "task_solution_id": f"solution_{task_id}",
                    "task_id": task_id,
                    "agent_run_id": agent_run_id,
                    "round_id": f"round_{round_num:03d}",
                    "miner_uid": miners[agent_idx]["uid"],  # Required field
                    "validator_uid": validator["uid"],  # Required field
                    "actions_taken": random.randint(2, 8),
                    "successful_actions": random.randint(1, task["expected_actions"]),
                    "failed_actions": random.randint(0, 2),
                    "execution_time": round(random.uniform(5, 60), 2),
                    "screenshots": [f"screenshot_{task_id}_1.png", f"screenshot_{task_id}_2.png"],
                    "logs": [f"Action 1: Click button", f"Action 2: Fill form", f"Action 3: Submit"],
                    "created_at": round_start + random.randint(300, 600),
                    "status": "completed" if random.random() > 0.1 else "failed"
                }
                task_solutions.append(task_solution)
                
                # Generate evaluation result
                score = round(random.uniform(0.3, 0.95), 3)
                evaluation_result = {
                    "evaluation_id": f"eval_{task_id}",
                    "task_id": task_id,
                    "task_solution_id": task_solution["task_solution_id"],  # Required field
                    "agent_run_id": agent_run_id,
                    "round_id": f"round_{round_num:03d}",
                    "miner_uid": miners[agent_idx]["uid"],  # Required field
                    "validator_uid": validator["uid"],  # Required field
                    "final_score": score,
                    "test_results_matrix": [[{"success": random.random() > 0.2, "extra_data": None}] for _ in range(3)],  # Required field
                    "execution_history": [f"Action {i+1}" for i in range(task_solution["actions_taken"])],  # Required field
                    "feedback": {
                        "task_prompt": task["prompt"],
                        "final_score": score,
                        "executed_actions": task_solution["actions_taken"],
                        "failed_actions": task_solution["failed_actions"],
                        "passed_tests": random.randint(1, 5),
                        "failed_tests": random.randint(0, 2),
                        "total_execution_time": task_solution["execution_time"],
                        "time_penalty": round(random.uniform(0, 0.1), 3),
                        "critical_test_penalty": random.randint(0, 1),
                        "test_results": [
                            {
                                "success": random.random() > 0.2,  # Required field
                                "extra_data": None
                            }
                            for i in range(random.randint(3, 7))
                        ],
                        "execution_history": [
                            {
                                "action": f"Action {i+1}",
                                "timestamp": round_start + random.randint(0, 300),
                                "success": random.random() > 0.1,
                                "details": f"Action {i+1} details"
                            }
                            for i in range(task_solution["actions_taken"])
                        ]
                    },
                    "evaluation_time": round(random.uniform(1, 10), 2)
                }
                evaluation_results.append(evaluation_result)
    
    return {
        "rounds": rounds,
        "agent_evaluation_runs": agent_evaluation_runs,
        "tasks": tasks,
        "task_solutions": task_solutions,
        "evaluation_results": evaluation_results
    }

def main():
    """Generate and save complete rounds 1-20 data."""
    print("Generating complete rounds 1-20 data...")
    
    # Generate all data
    data = generate_complete_rounds_data()
    
    # Save each collection
    base_path = "/home/usuario1/autoppia/autoppia_bittensor_dashboard_backend/data/mock"
    
    for collection_name, collection_data in data.items():
        output_file = f"{base_path}/{collection_name}.json"
        with open(output_file, 'w') as f:
            json.dump(collection_data, f, indent=2)
        print(f"✅ Saved {len(collection_data)} {collection_name} to {output_file}")
    
    # Print summary
    print(f"\n📊 Summary:")
    print(f"  Rounds: {len(data['rounds'])}")
    print(f"  Agent Evaluation Runs: {len(data['agent_evaluation_runs'])}")
    print(f"  Tasks: {len(data['tasks'])}")
    print(f"  Task Solutions: {len(data['task_solutions'])}")
    print(f"  Evaluation Results: {len(data['evaluation_results'])}")
    
    # Show sample round
    sample_round = data['rounds'][0]
    print(f"\n🔍 Sample Round ({sample_round['round_id']}):")
    print(f"  Tasks: {sample_round['n_tasks']}")
    print(f"  Miners: {sample_round['n_miners']}")
    print(f"  Winners: {sample_round['n_winners']}")
    print(f"  Top Score: {sample_round['winners'][0]['score'] if sample_round['winners'] else 'N/A'}")

if __name__ == "__main__":
    main()
