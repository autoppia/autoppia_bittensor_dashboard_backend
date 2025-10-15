#!/usr/bin/env python3
"""
Generate complete rounds 1-20 data including all related collections.
"""
import json
import random
import time
import uuid
from datetime import datetime, timedelta

ASSET_BASE = "https://assets.autoppia.com"

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
        n_miners = 30  # Fixed to 30 miners per round
        n_winners = random.randint(5, 10)  # More winners since we have more miners
        
        # Define SOTA benchmark agents (no UID/hotkey)
        sota_agents = [
            {
                "agent_name": "OpenAI GPT-4o",
                "agent_image": f"{ASSET_BASE}/agents/sota_openai_gpt4o.png",
                "github": "https://github.com/openai/gpt-4o",
                "description": "OpenAI's flagship multimodal benchmark agent",
                "provider": "OpenAI",
                "is_sota": True
            },
            {
                "agent_name": "Claude 3.5 Sonnet",
                "agent_image": f"{ASSET_BASE}/agents/sota_claude_35_sonnet.png",
                "github": "https://github.com/anthropic/claude-3-5-sonnet",
                "description": "Anthropic Claude benchmark agent for enterprise workflows",
                "provider": "Anthropic",
                "is_sota": True
            },
            {
                "agent_name": "Browser Use",
                "agent_image": f"{ASSET_BASE}/agents/sota_browser_use.png",
                "github": "https://github.com/browser-use/browser-use",
                "description": "Community browser-use agent for autonomous browsing",
                "provider": "Community",
                "is_sota": True
            }
        ]

        # Generate miners list with random UIDs from 0-255 (real miners only)
        miners = []
        used_uids = set()

        featured_miners = [
            {
                "uid": 123,
                "hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                "coldkey": None,
                "agent_name": "Autoppia Bittensor",
                "agent_image": f"{ASSET_BASE}/agents/autoppia_bittensor.png",
                "github": "https://github.com/autoppia/bittensor-agent",
                "is_sota": False
            },
            {
                "uid": 145,
                "hotkey": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
                "coldkey": None,
                "agent_name": "Tao Labs Alpha",
                "agent_image": f"{ASSET_BASE}/agents/tao_labs_alpha.png",
                "github": "https://github.com/taolabs/alpha-agent",
                "is_sota": False
            },
            {
                "uid": 178,
                "hotkey": "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy",
                "coldkey": None,
                "agent_name": "RoundTable Automator",
                "agent_image": f"{ASSET_BASE}/agents/roundtable_automator.png",
                "github": "https://github.com/roundtable/automator",
                "is_sota": False
            }
        ]

        for miner in featured_miners:
            miners.append(miner)
            used_uids.add(miner["uid"])

        while len(miners) < n_miners:
            uid = random.randint(0, 255)
            if uid in used_uids:
                continue
            used_uids.add(uid)
            miners.append({
                "uid": uid,
                "hotkey": f"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY{uid:02d}",
                "coldkey": None,
                "agent_name": f"Agent {uid}",
                "agent_image": f"{ASSET_BASE}/agents/agent_{uid}.png",
                "github": f"https://github.com/agent{uid}/autoppia-agent",
                "is_sota": False
            })

        # Determine winners among real miners only
        winners = []
        winner_pool = random.sample(miners, min(n_winners, len(miners)))
        for miner in winner_pool:
            base = random.uniform(0.65, 0.95)
            winners.append({
                "miner_uid": miner["uid"],
                "score": round(base, 3),
                "rank": 0,
                "reward": round(base * 100, 2)
            })

        winners.sort(key=lambda x: x["score"], reverse=True)
        for i, winner in enumerate(winners):
            winner["rank"] = i + 1
        
        # Create round data with 4 validators per round
        base_validators = [
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
        
        # Generate additional validators for variety
        additional_validators = []
        for i in range(10):  # Generate 10 additional validators to choose from
            additional_validators.append({
                "uid": 126 + i,
                "hotkey": f"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY{i:02d}",
                "coldkey": None,
                "stake": round(random.uniform(800, 2000), 0),
                "vtrust": round(random.uniform(0.6, 0.95), 2),
                "name": f"Validator{i+1}",
                "version": f"{random.randint(5, 7)}.{random.randint(0, 9)}.{random.randint(0, 9)}"
            })
        
        # Select 4 validators for this round (mix of base and additional)
        round_validators = []
        # Always include at least one base validator
        round_validators.append(random.choice(base_validators))
        # Add 3 more validators from the pool
        all_validators = base_validators + additional_validators
        remaining_validators = [v for v in all_validators if v not in round_validators]
        round_validators.extend(random.sample(remaining_validators, min(3, len(remaining_validators))))
        
        # Use the first validator as the primary validator for agent runs
        primary_validator = round_validators[0]
        
        weight_lookup = {miner["uid"]: round(random.uniform(0.1, 1.0), 3) for miner in miners}
        round_data = {
            "validator_round_id": f"round_{round_num:03d}",
            "validators": round_validators,
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
            "sota_agents": sota_agents,
            "winners": winners,
            "winner_scores": [w["score"] for w in winners],
            "weights": weight_lookup,
            "average_score": round(sum(w["score"] for w in winners) / len(winners), 3) if winners else 0.0,
            "top_score": max(w["score"] for w in winners) if winners else 0.0,
            "status": "completed" if round_num < 20 else "active"
        }
        rounds.append(round_data)
        
        winner_lookup = {winner["miner_uid"]: winner for winner in winners}
        combined_agents = [
            {"profile": miner, "is_sota": False} for miner in miners
        ] + [{"profile": agent, "is_sota": True} for agent in sota_agents]

        # Generate agent evaluation runs for this round
        for agent_idx, agent_entry in enumerate(combined_agents):
            agent_profile = agent_entry["profile"]
            is_sota = agent_entry["is_sota"]
            agent_run_id = f"round_{round_num:03d}_{agent_idx + 1}"
            miner_uid = agent_profile.get("uid") if not is_sota else None
            hotkey = agent_profile.get("hotkey") if not is_sota else None

            base_score = random.uniform(0.5, 0.92)
            if is_sota:
                base_score = random.uniform(0.9, 0.97)

            winner_info = winner_lookup.get(miner_uid)
            agent_run = {
                "agent_run_id": agent_run_id,
                "validator_round_id": f"round_{round_num:03d}",
                "validator_uid": primary_validator["uid"],
                "miner_uid": miner_uid,
                "is_sota": is_sota,
                "miner_info": {
                    "uid": miner_uid,
                    "hotkey": hotkey,
                    "agent_name": agent_profile["agent_name"],
                    "agent_image": agent_profile.get("agent_image", ""),
                    "github": agent_profile.get("github", ""),
                    "is_sota": is_sota,
                    "description": agent_profile.get("description"),
                    "provider": agent_profile.get("provider")
                },
                "version": "1.0",
                "started_at": round_start + random.randint(0, 300),
                "ended_at": round_end - random.randint(0, 300),
                "elapsed_sec": max(60, round_end - round_start - random.randint(0, 300)),
                "avg_eval_score": round(base_score, 3),
                "avg_execution_time": round(random.uniform(30, 120), 2),
                "avg_reward": round(base_score * random.uniform(80, 120), 2),
                "rank": None if is_sota else (winner_info["rank"] if winner_info else None),
                "weight": None if is_sota else weight_lookup.get(miner_uid)
            }
            agent_evaluation_runs.append(agent_run)

            # Generate tasks for this agent run (SOTA agents included for comparison)
            for task_idx in range(n_tasks):
                task_id = f"task_{round_num:03d}_{agent_idx + 1}_{task_idx + 1}"
                task = {
                    "task_id": task_id,
                    "agent_run_id": agent_run_id,
                    "validator_round_id": f"round_{round_num:03d}",
                    "url": f"https://example{random.randint(1, 10)}.com",
                    "prompt": f"Task {task_idx + 1} for round {round_num}: {random.choice(['Login to website', 'Fill out form', 'Navigate to page', 'Extract data'])}",
                    "task_type": random.choice(["login", "navigation", "form_filling", "data_extraction"]),
                    "created_at": round_start + random.randint(0, 300),
                    "status": "completed" if random.random() > 0.1 else "failed",
                    "difficulty": random.choice(["easy", "medium", "hard"]),
                    "expected_actions": random.randint(3, 8),
                    "time_limit": random.randint(60, 300)
                }
                tasks.append(task)

                task_solution = {
                    "task_solution_id": f"solution_{task_id}",
                    "task_id": task_id,
                    "agent_run_id": agent_run_id,
                    "validator_round_id": f"round_{round_num:03d}",
                    "miner_uid": miner_uid,
                    "validator_uid": primary_validator["uid"],
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

                score = round(random.uniform(0.3, 0.95) if not is_sota else random.uniform(0.85, 0.99), 3)
                evaluation_result = {
                    "evaluation_id": f"eval_{task_id}",
                    "task_id": task_id,
                    "task_solution_id": task_solution["task_solution_id"],
                    "agent_run_id": agent_run_id,
                    "validator_round_id": f"round_{round_num:03d}",
                    "miner_uid": miner_uid,
                    "validator_uid": primary_validator["uid"],
                    "final_score": score,
                    "test_results_matrix": [[{"success": random.random() > 0.2, "extra_data": None}] for _ in range(3)],
                    "execution_history": [f"Action {i+1}" for i in range(task_solution["actions_taken"])],
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
                                "success": random.random() > 0.2,
                                "extra_data": None
                            }
                            for _ in range(random.randint(3, 7))
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
    print(f"\n🔍 Sample Round ({sample_round['validator_round_id']}):")
    print(f"  Tasks: {sample_round['n_tasks']}")
    print(f"  Miners: {sample_round['n_miners']}")
    print(f"  Winners: {sample_round['n_winners']}")
    print(f"  Top Score: {sample_round['winners'][0]['score'] if sample_round['winners'] else 'N/A'}")

if __name__ == "__main__":
    main()
